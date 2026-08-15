NODE_INTENT = "intent"
NODE_AGENTS = "agents"
NODE_CLAUDE = "claude"
NODE_COMPLETE = "complete"

# The pipeline is a fixed sequence for every message - Intent -> Agents -> Claude -> Complete -
# so there's no branching to decide (see workflow/graph.py). NODE_CLAUDE only announces that
# synthesis is starting (instant); NODE_COMPLETE is the one that actually calls Claude and
# finishes the turn - splitting them gives .stream() a genuine, observable "Claude is running"
# frame instead of jumping straight from "agents done" to "everything done" (see
# dashboard/chat.py's progress strip).
ALL_STAGES = [NODE_INTENT, NODE_AGENTS, NODE_CLAUDE, NODE_COMPLETE]
