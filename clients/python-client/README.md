# Cliente Python - Juego de Memoria gRPC

Esta carpeta se comparte directamente con cada companero para que se conecte al servidor desde su PC.

## 1) Requisitos

- Python 3.11+
- Acceso de red al servidor (IP y puerto 50051)

## 2) Instalacion

```bash
python -m venv .venv
source .venv/bin/activate
# Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## 3) Generar stubs

Windows PowerShell:

```powershell
./scripts/generate_proto.ps1
```

Linux/macOS:

```bash
bash scripts/generate_proto.sh
```

## 4) Ejecutar cliente

```bash
python client.py --host 192.168.1.40 --port 50051 --name Ana
```

Ejecuta otro cliente con otro nombre en otra terminal/PC:

```bash
python client.py --host 192.168.1.40 --port 50051 --name Luis
```

## 5) Comandos dentro del cliente

- `estado`
- `jugar <r1> <c1> <r2> <c2>`
- `stats`
- `historial`
- `ayuda`
- `salir`

## 6) Ejecutar cliente con Docker (opcional)

Construir imagen:

```bash
docker build -t memory-client -f Dockerfile.client .
```

Ejecutar (usa host del servidor en red LAN):

```bash
docker run --rm -it memory-client --host 192.168.1.40 --port 50051 --name Carla
```
