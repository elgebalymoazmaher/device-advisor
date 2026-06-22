from __future__ import annotations

import gradio as gr


def recommend(message: str, history: list[dict]) -> str:
    return "Hello from DeviceAdvisor — recommendation engine coming soon."


if __name__ == "__main__":
    gr.ChatInterface(recommend, title="DeviceAdvisor").launch()
