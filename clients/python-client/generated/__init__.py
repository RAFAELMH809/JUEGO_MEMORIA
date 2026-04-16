from __future__ import annotations

import sys
from pathlib import Path

# grpc_tools usa import absoluto en *_pb2_grpc.py.
# Esto habilita resolver memory_game_pb2 desde generated.
_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
	sys.path.insert(0, _THIS_DIR)
