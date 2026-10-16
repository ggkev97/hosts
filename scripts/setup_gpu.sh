#!/usr/bin/env bash
# One-shot setup for a fresh Ubuntu 22.04 NVIDIA GPU machine (RunPod, Lambda, Vast.ai, ...).
#   bash scripts/setup_gpu.sh && ./scripts/run.sh
# Untested on real hardware: if a step fails, send the output back.
set -euo pipefail
cd "$(dirname "$0")/.."

nvidia-smi >/dev/null || { echo "No NVIDIA GPU / driver found"; exit 1; }

sudo apt-get update -y
sudo apt-get install -y ffmpeg colmap git python3-venv python3-dev build-essential ninja-build wget

python3 -m venv .venv
. .venv/bin/activate
pip install -U pip wheel
pip install -r requirements.txt

# PyTorch (CUDA 12.1 wheels), Nerfstudio (splatfacto), SAM 2, CLIP.
pip install torch==2.1.2 torchvision==0.16.2 --index-url https://download.pytorch.org/whl/cu121
pip install nerfstudio
pip install "git+https://github.com/facebookresearch/sam2.git"
pip install transformers

mkdir -p checkpoints
[ -f checkpoints/sam2.1_hiera_large.pt ] || wget -q -O checkpoints/sam2.1_hiera_large.pt \
  https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt

cat > scripts/env.sh <<ENV
export SAM2_CHECKPOINT="$PWD/checkpoints/sam2.1_hiera_large.pt"
export SAM2_CONFIG="configs/sam2.1/sam2.1_hiera_l.yaml"
ENV
python tools/make_catalogue.py
for t in ffmpeg colmap ns-process-data ns-train ns-export; do command -v $t >/dev/null || echo "MISSING: $t"; done
python -c "import torch, sam2; print('cuda:', torch.cuda.is_available())"
.venv/bin/pytest -q
echo "Setup done. Start the server with: ./scripts/run.sh"
