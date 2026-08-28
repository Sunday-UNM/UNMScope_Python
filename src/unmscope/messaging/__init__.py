"""Inter-task messaging.

asyncio.Queue-based analog of the LabVIEW named-queue message-passing
architecture (Common/Main MsgQ, SPIM Inter Loop Messaging): each task (GUI,
Engine, Image Acq, File I/O, Debug) owns a queue that others send typed
messages to. Not yet implemented -- see ROADMAP.md Phase 1.
"""
