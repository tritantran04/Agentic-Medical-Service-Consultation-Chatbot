import os
import re
import json
from typing import Any, Dict, AsyncIterable, List, Literal, Optional

from pydantic import BaseModel
from dotenv import load_dotenv, find_dotenv

from langchain_core.messages import (
    SystemMessage, HumanMessage, AIMessage, ToolMessage, RemoveMessage, BaseMessage,
)
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_groq import ChatGroq
try:
    from .data import search_packages, get_package_by_id, build_or_load_index
    from .prompts import SUMMARY_HISTORY, SYSTEM_MESSAGE, RETRIEVAL_INSTRUCTION, NO_RETRIEVAL_INSTRUCTION, NO_TOOL_CALL_WARNING
except ImportError:
    from data import search_packages, get_package_by_id, build_or_load_index
    from prompts import SUMMARY_HISTORY, SYSTEM_MESSAGE, RETRIEVAL_INSTRUCTION, NO_RETRIEVAL_INSTRUCTION, NO_TOOL_CALL_WARNING

load_dotenv(find_dotenv())

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

llm_main = ChatGroq(model="openai/gpt-oss-120b", temperature=0, api_key=GROQ_API_KEY)
llm_answer = ChatGroq(model="openai/gpt-oss-20b", temperature=0, api_key=GROQ_API_KEY)


class ResponseFormat(BaseModel):
    """Định dạng phản hồi chuẩn hóa của hệ thống."""
    status: Literal["input_required", "completed", "error"] = "input_required"
    message: str

class AgentState(MessagesState):
    structured_response: Optional[ResponseFormat]


# RAG
@tool
def search_medical_packages(query: str) -> str:
    """Tìm các gói khám sức khỏe phù hợp với triệu chứng/nhu cầu của người dùng.

    Args:
        query: câu mô tả triệu chứng, độ tuổi, giới tính, hoặc nhu cầu khám của người dùng.
    """
    ids = search_packages(query)
    if not ids:
        return "Không tìm thấy gói khám nào phù hợp với nhu cầu này trong dữ liệu hiện có."
    return get_package_by_id(ids)



K_TURNS = 2  

def _get_text(m: BaseMessage) -> str:
    content = m.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(i.get("text", "") for i in content if isinstance(i, dict) and i.get("type") == "text")
    return ""

def _split_into_turns(messages: List[BaseMessage]) -> List[List[BaseMessage]]:
    """Gom messages thành từng lượt hỏi-đáp (gồm cả các message gọi tool bên trong lượt đó)."""
    turns, current = [], []
    for m in messages:
        if isinstance(m, HumanMessage) and current:
            turns.append(current)
            current = [m]
        else:
            current.append(m)
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def summarize_history(state: AgentState):
    messages = state["messages"]
    turns = _split_into_turns(messages)

    if len(turns) <= K_TURNS:
        return {}

    old_turns, keep_turns = turns[:-K_TURNS], turns[-K_TURNS:]
    old = [m for t in old_turns for m in t]
    keep = [m for t in keep_turns for m in t]

    history_text = "\n".join(
        f"{'Người dùng' if isinstance(m, HumanMessage) else 'Bot'}: {_get_text(m)}"
        for m in old
        if isinstance(m, (HumanMessage, AIMessage))
        and not (isinstance(m, AIMessage) and getattr(m, "tool_calls", None))
        and _get_text(m)
    )

    summary_resp = llm_main.invoke([
        SystemMessage(content=SUMMARY_HISTORY),
        HumanMessage(content=history_text),
    ])
    summary_text = re.sub(r"<think>.*?</think>", "", summary_resp.content, flags=re.DOTALL).strip()
    summary_msg = SystemMessage(content=f"[Tóm tắt hội thoại trước đó]: {summary_text}")

    delete = [RemoveMessage(id=m.id) for m in messages]
    return {"messages": delete + [summary_msg] + keep}


# Route ___________________________________________________________________________
def agent_node(state: AgentState):
    llm_with_tools = llm_main.bind_tools([search_medical_packages])
    response = llm_with_tools.invoke([SystemMessage(content=SYSTEM_MESSAGE)] + state["messages"])
    return {"messages": [response]}


def route_decision(state: AgentState) -> Literal["retrieve", "generate_answer"]:
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "retrieve"
    return "generate_answer"
# _________________________________________________________________________________

def _get_latest_tool_context(state: AgentState):

    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage):
            return msg.content
        if isinstance(msg, HumanMessage):
            return None
    return None


def generate_answer(state: AgentState):
    context = _get_latest_tool_context(state)
    instruction = RETRIEVAL_INSTRUCTION if context else NO_RETRIEVAL_INSTRUCTION
    gen_prompt = (
        f"NHIỆM VỤ: {instruction}\n\n"
        f"NGỮ CẢNH (từ tool, nếu có):\n{context or '(không có)'}\n\n"
        f"{NO_TOOL_CALL_WARNING}\n" # Fix model's output error. Delete if change model 
        "CHỈ trả lời bằng ĐÚNG một đối tượng JSON hợp lệ, không kèm chữ nào khác, "
        "không dùng markdown code block, theo đúng định dạng:\n"
        '{"status": "input_required" | "completed" | "error", "message": "<câu trả lời cho người dùng>"}'
    )

    raw = llm_answer.invoke(
        [SystemMessage(content=SYSTEM_MESSAGE), HumanMessage(content=gen_prompt)] + state["messages"]
    )
    cleaned = re.sub(r"```json|```", "", raw.content or "")
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()

    try:
        data = json.loads(cleaned)
        response = ResponseFormat(
            status=data.get("status", "completed"),
            message=data.get("message") or cleaned,
        )
    except Exception:
        response = ResponseFormat(status="completed", message=raw.content or "Xin lỗi, tôi chưa hiểu ý bạn.")

    return {
        "messages": [AIMessage(content=response.message)],
        "structured_response": response,
    }


def build_graph() -> StateGraph:
    build_or_load_index()  # đảm bảo Chroma index sẵn sàng trước khi phục vụ request

    memory = MemorySaver()
    builder = StateGraph(AgentState)

    builder.add_node("summarize_history", summarize_history)
    builder.add_node("agent", agent_node)
    builder.add_node("retrieve", ToolNode([search_medical_packages]))
    builder.add_node("generate_answer", generate_answer)

    builder.add_edge(START, "summarize_history")
    builder.add_edge("summarize_history", "agent")
    builder.add_conditional_edges("agent", route_decision, {
        "retrieve": "retrieve",
        "generate_answer": "generate_answer",
    })
    builder.add_edge("retrieve", "generate_answer")
    builder.add_edge("generate_answer", END)

    return builder.compile(checkpointer=memory)

# AGENT
class ServiceAgent:
    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

    def __init__(self):
        self.graph = build_graph()

    def invoke(self, query, sessionId) -> Dict[str, Any]:
        config = {"configurable": {"thread_id": sessionId}}
        self.graph.invoke({"messages": [HumanMessage(content=query)]}, config)
        return self.get_agent_response(config)

    async def stream(self, query, sessionId) -> AsyncIterable[Dict[str, Any]]:
        inputs = {"messages": [HumanMessage(content=query)]}
        config = {"configurable": {"thread_id": sessionId}}

        for item in self.graph.stream(inputs, config, stream_mode="values"):
            message = item["messages"][-1]
            if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
                yield {
                    "is_task_complete": False,
                    "require_user_input": False,
                    "content": "Đang tìm gói khám phù hợp...",
                }
            elif isinstance(message, ToolMessage):
                yield {
                    "is_task_complete": False,
                    "require_user_input": False,
                    "content": "Đang xử lý kết quả tìm kiếm...",
                }

        yield self.get_agent_response(config)

    def get_agent_response(self, config):
        current_state = self.graph.get_state(config)
        structured_response = current_state.values.get("structured_response")
        if isinstance(structured_response, ResponseFormat):
            return {
                "is_task_complete": structured_response.status == "completed",
                "require_user_input": structured_response.status in ("input_required", "error"),
                "content": structured_response.message,
            }
        return {
            "is_task_complete": False,
            "require_user_input": True,
            "content": "Hệ thống không thể xử lý yêu cầu lúc này. Vui lòng thử lại.",
        }

if __name__ == "__main__":
    agent = ServiceAgent()
    thread_id = "Test_1"
    print("Gõ 'exit' để thoát.")
    while True:
        q = input("Bạn: ").strip()
        if q.lower() in ["exit", "quit", "q"]:
            break
        if not q:
            continue
        result = agent.invoke(q, thread_id)
        print("Bot:", result["content"])