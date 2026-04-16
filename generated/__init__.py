from __future__ import annotations

import sys
from pathlib import Path

# grpc_tools genera en memory_game_pb2_grpc.py un import absoluto
# `import memory_game_pb2`. Este bootstrap garantiza que el directorio
# `generated` este en sys.path para resolverlo en local y en Docker.
_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
	sys.path.insert(0, _THIS_DIR)
