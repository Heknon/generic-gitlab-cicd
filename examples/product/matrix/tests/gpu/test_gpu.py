import shutil

def test_gpu_tool_available():
    assert shutil.which("nvidia-smi"), "Requires a GPU runner with NVIDIA tools"
