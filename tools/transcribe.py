#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地转写：faster-whisper
用法: python tools/transcribe.py <音频或视频> [模型] [输出前缀]
模型: base / small / medium / large-v3
"""
import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from faster_whisper import WhisperModel  # noqa: E402


def main():
    audio = sys.argv[1]
    size = sys.argv[2] if len(sys.argv) > 2 else "small"
    out_prefix = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(audio)[0]

    device = os.environ.get("WHISPER_DEVICE", "cpu")
    compute = "int8" if device == "cpu" else "float16"
    # CPU 上 beam=5 太慢（实测 0.4 倍速）；赶时间用 WHISPER_BEAM=1、WHISPER_THREADS=<核数>
    beam = int(os.environ.get("WHISPER_BEAM", "5"))
    threads = int(os.environ.get("WHISPER_THREADS", "0"))

    t0 = time.time()
    print(f"[whisper] model={size} device={device} compute={compute}", flush=True)
    model = WhisperModel(size, device=device, compute_type=compute,
                         cpu_threads=threads,
                         download_root=os.path.join(os.path.dirname(__file__), "models"))
    print(f"[whisper] model ready in {time.time()-t0:.1f}s", flush=True)

    # 领域词表：提升专业词识别率（否则"捕获"会被听成"补货"）
    # 换课程时用环境变量 WHISPER_PROMPT 或第 4 个参数覆盖
    PROMPT = os.environ.get("WHISPER_PROMPT")
    if not PROMPT:
        PROMPT = sys.argv[4] if len(sys.argv) > 4 else (
            "以下是 STM32 单片机 HAL 库教学视频。涉及术语："
            "定时器、计数器、预分频器、自动重装载寄存器、捕获、输入捕获、输出比较、"
            "占空比、脉宽、上升沿、下降沿、通道、寄存器、中断、回调、使能、寄存器位、"
            "时钟树、分频、倍频、GPIO、推挽、开漏、上拉、下拉、串口、波特率、"
            "SPI、I2C、ADC、编码器、PWM、NVIC、优先级、标志位、清零。"
        )

    segments, info = model.transcribe(
        audio, language="zh", beam_size=beam, vad_filter=True,
        initial_prompt=PROMPT,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    print(f"[whisper] duration={info.duration:.1f}s", flush=True)

    lines = []
    srt = []
    for i, seg in enumerate(segments, 1):
        text = seg.text.strip()
        lines.append(text)
        srt.append(f"{i}\n{fmt(seg.start)} --> {fmt(seg.end)}\n{text}\n")
        if i % 20 == 0:
            print(f"[whisper] {seg.end:.0f}s ...", flush=True)

    with open(out_prefix + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(out_prefix + ".srt", "w", encoding="utf-8") as f:
        f.write("\n".join(srt))

    print(f"[whisper] done, {len(lines)} segments, {time.time()-t0:.0f}s total", flush=True)
    print(out_prefix + ".txt")


def fmt(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = int(t % 60); ms = int((t % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


if __name__ == "__main__":
    main()
