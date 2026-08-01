from app.core.agents.chat import ChatSession
session = ChatSession("test")
print("Expanded:", session._rewrite_query_for_search("draft me a mail to kritiman_ug_24@ee.nits.ac.in saying hi"))
