from flask import Flask, request, jsonify, render_template
from agent.agent import ServiceAgent 
import uuid

app = Flask(__name__)
service_agent = ServiceAgent()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not message:
        return jsonify({"error": "Empty message"}), 400

    result = service_agent.invoke(message, session_id)


    reply = result["content"].replace("\\n", "\n")
    return jsonify({"reply": reply, "session_id": session_id})


if __name__ == "__main__":
    app.run(debug=True, port=2222)
