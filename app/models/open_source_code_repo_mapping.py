"""开源仓库路径前缀 → GitHub remote 映射（含 44 条初始化数据）。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time_utils import beijing_now

# 附录 B：44 条冻结映射
REPO_MAPPING_SEED = [
    ("nvidia/cupy__cupy", "NVIDIA", "cupy", "https://github.com/NVIDIA/cupy"),
    ("nvidia/CVCUDA__CV-CUDA", "NVIDIA", "CV-CUDA", "https://github.com/NVIDIA/CV-CUDA"),
    ("nvidia/DPDK__dpdk", "DPDK", "dpdk", "https://github.com/DPDK/dpdk"),
    ("nvidia/flang-compiler__flang", "flang-compiler", "flang", "https://github.com/flang-compiler/flang"),
    ("nvidia/nv-legate__cupynumeric", "nv-legate", "cupynumeric", "https://github.com/nv-legate/cupynumeric"),
    ("nvidia/nv-morpheus__Morpheus", "NVIDIA", "Morpheus", "https://github.com/NVIDIA/Morpheus"),
    ("nvidia/NVIDIA__AMGX", "NVIDIA", "AMGX", "https://github.com/NVIDIA/AMGX"),
    ("nvidia/NVIDIA__apex", "NVIDIA", "apex", "https://github.com/NVIDIA/apex"),
    ("nvidia/NVIDIA__cub", "NVIDIA", "cub", "https://github.com/NVIDIA/cub"),
    ("nvidia/NVIDIA__cuQuantum", "NVIDIA", "cuQuantum", "https://github.com/NVIDIA/cuQuantum"),
    ("nvidia/NVIDIA__cutlass", "NVIDIA", "cutlass", "https://github.com/NVIDIA/cutlass"),
    ("nvidia/NVIDIA__DALI", "NVIDIA", "DALI", "https://github.com/NVIDIA/DALI"),
    ("nvidia/NVIDIA__FasterTransformer", "NVIDIA", "FasterTransformer", "https://github.com/NVIDIA/FasterTransformer"),
    ("nvidia/NVIDIA__gdrcopy", "NVIDIA", "gdrcopy", "https://github.com/NVIDIA/gdrcopy"),
    ("nvidia/NVIDIA__MatX", "NVIDIA", "MatX", "https://github.com/NVIDIA/MatX"),
    ("nvidia/NVIDIA__MDL-SDK", "NVIDIA", "MDL-SDK", "https://github.com/NVIDIA/MDL-SDK"),
    ("nvidia/NVIDIA__nccl", "NVIDIA", "nccl", "https://github.com/NVIDIA/nccl"),
    ("nvidia/NVIDIA__nccl-tests", "NVIDIA", "nccl-tests", "https://github.com/NVIDIA/nccl-tests"),
    ("nvidia/NVIDIA__NVFlare", "NVIDIA", "NVFlare", "https://github.com/NVIDIA/NVFlare"),
    (
        "nvidia/NVIDIA__open-gpu-kernel-modules",
        "NVIDIA",
        "open-gpu-kernel-modules",
        "https://github.com/NVIDIA/open-gpu-kernel-modules",
    ),
    ("nvidia/NVIDIA__spark-rapids", "NVIDIA", "spark-rapids", "https://github.com/NVIDIA/spark-rapids"),
    (
        "nvidia/NVIDIA__TransformerEngine",
        "NVIDIA",
        "TransformerEngine",
        "https://github.com/NVIDIA/TransformerEngine",
    ),
    (
        "nvidia/NVIDIA__trt-samples-for-hackathon-cn",
        "NVIDIA",
        "trt-samples-for-hackathon-cn",
        "https://github.com/NVIDIA/trt-samples-for-hackathon-cn",
    ),
    ("nvidia/NVIDIA__warp", "NVIDIA", "warp", "https://github.com/NVIDIA/warp"),
    (
        "nvidia/NVIDIA-AI-IOT__deepstream_tao_apps",
        "NVIDIA-AI-IOT",
        "deepstream_tao_apps",
        "https://github.com/NVIDIA-AI-IOT/deepstream_tao_apps",
    ),
    (
        "nvidia/NVIDIA-AI-IOT__yolo_deepstream",
        "NVIDIA-AI-IOT",
        "yolo_deepstream",
        "https://github.com/NVIDIA-AI-IOT/yolo_deepstream",
    ),
    ("nvidia/nvidia-isaac__nvblox", "nvidia-isaac", "nvblox", "https://github.com/nvidia-isaac/nvblox"),
    (
        "nvidia/NVIDIA-Merlin__HugeCTR",
        "NVIDIA-Merlin",
        "HugeCTR",
        "https://github.com/NVIDIA-Merlin/HugeCTR",
    ),
    ("nvidia/NVIDIA-RTX__NVRHI", "NVIDIA-RTX", "NVRHI", "https://github.com/NVIDIA-RTX/NVRHI"),
    (
        "nvidia/NVIDIA-RTX__Streamline",
        "NVIDIA-RTX",
        "Streamline",
        "https://github.com/NVIDIA-RTX/Streamline",
    ),
    (
        "nvidia/nvidiagameworks__falcor",
        "nvidiagameworks",
        "falcor",
        "https://github.com/nvidiagameworks/falcor",
    ),
    ("nvidia/onnx__onnx-tensorrt", "onnx", "onnx-tensorrt", "https://github.com/onnx/onnx-tensorrt"),
    ("nvidia/pytorch__TensorRT", "pytorch", "TensorRT", "https://github.com/pytorch/TensorRT"),
    ("nvidia/rapidsai__cucim", "rapidsai", "cucim", "https://github.com/rapidsai/cucim"),
    ("nvidia/rapidsai__cudf", "rapidsai", "cudf", "https://github.com/rapidsai/cudf"),
    ("nvidia/rapidsai__cugraph", "rapidsai", "cugraph", "https://github.com/rapidsai/cugraph"),
    ("nvidia/rapidsai__cuspatial", "rapidsai", "cuspatial", "https://github.com/rapidsai/cuspatial"),
    ("nvidia/rapidsai__raft", "rapidsai", "raft", "https://github.com/rapidsai/raft"),
    ("nvidia/rapidsai__rmm", "rapidsai", "rmm", "https://github.com/rapidsai/rmm"),
    ("nvidia/shader-slang__slang", "shader-slang", "slang", "https://github.com/shader-slang/slang"),
    ("nvidia/tensorflow__tensorflow", "tensorflow", "tensorflow", "https://github.com/tensorflow/tensorflow"),
    (
        "nvidia/triton-inference-server__backend",
        "triton-inference-server",
        "backend",
        "https://github.com/triton-inference-server/backend",
    ),
    (
        "nvidia/triton-inference-server__client",
        "triton-inference-server",
        "client",
        "https://github.com/triton-inference-server/client",
    ),
    (
        "nvidia/triton-inference-server__server",
        "triton-inference-server",
        "server",
        "https://github.com/triton-inference-server/server",
    ),
]


class OpenSourceCodeRepoMapping(Base):
    __tablename__ = "open_source_code_repo_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path_prefix: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    org_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    repo_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    remote_base_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now, nullable=False)


async def seed_repo_mappings(session) -> int:
    """INSERT IGNORE 风格：已存在 path_prefix 则跳过。返回新插入条数。"""
    inserted = 0
    for path_prefix, org_name, repo_name, remote_base_url in REPO_MAPPING_SEED:
        exists = await session.execute(
            select(OpenSourceCodeRepoMapping.id).where(
                OpenSourceCodeRepoMapping.path_prefix == path_prefix
            )
        )
        if exists.scalar_one_or_none() is not None:
            continue
        session.add(
            OpenSourceCodeRepoMapping(
                path_prefix=path_prefix,
                org_name=org_name,
                repo_name=repo_name,
                remote_base_url=remote_base_url,
            )
        )
        inserted += 1
    if inserted:
        await session.flush()
    return inserted
