"""AI explanations attach the question's local figures and refuse text-only models."""
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from starhope.ai_providers import resolve_text_provider


def _image(root: Path, name: str) -> str:
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 24), "white").save(root / name)
    return f"/static/{root.name}/{name}"


def _fake_post(captured):
    def post(provider, payload, **kwargs):
        captured["payload"] = payload
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"message": {"content": "结合图形可得答案。"}}],
        })
    return post


def test_solve_attaches_question_images(client, monkeypatch):
    import main

    client.headers["X-Local-Token"] = main.LOCAL_TOKEN
    url = _image(Path(main.UPLOAD_DIR), "solve-figure.png")
    provider = resolve_text_provider("DEEPSEEK/deepseek-flash", {"DEEPSEEK_API_KEY": "fake"})
    captured = {}
    monkeypatch.setattr(main, "resolve_text_provider", lambda model=None: provider)
    monkeypatch.setattr(main, "post_chat_completion", _fake_post(captured))

    response = client.post("/api/ai/solve", data={
        "content": "如图，求角A的度数。",
        "question_type": "detailed_answer",
        "stream": "false",
        "image_paths": json.dumps([url]),
    })
    assert response.status_code == 200, response.text
    user_content = captured["payload"]["messages"][1]["content"]
    assert isinstance(user_content, list)
    assert user_content[0]["type"] == "text"
    assert "附图是题设的一部分" in user_content[0]["text"]
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert response.json()["solution"]


def test_solve_collects_images_embedded_in_content(client, monkeypatch):
    import main

    client.headers["X-Local-Token"] = main.LOCAL_TOKEN
    url = _image(Path(main.UPLOAD_DIR), "solve-embedded.png")
    provider = resolve_text_provider("DEEPSEEK/deepseek-flash", {"DEEPSEEK_API_KEY": "fake"})
    captured = {}
    monkeypatch.setattr(main, "resolve_text_provider", lambda model=None: provider)
    monkeypatch.setattr(main, "post_chat_completion", _fake_post(captured))

    response = client.post("/api/ai/solve", data={
        "content": f"如图 ![]({url})，求阴影面积。",
        "question_type": "detailed_answer",
        "stream": "false",
    })
    assert response.status_code == 200, response.text
    user_content = captured["payload"]["messages"][1]["content"]
    assert any(part.get("type") == "image_url" for part in user_content)


def test_solve_rejects_text_only_model_when_images_present(client, monkeypatch):
    import main

    client.headers["X-Local-Token"] = main.LOCAL_TOKEN
    url = _image(Path(main.UPLOAD_DIR), "solve-text-model.png")
    provider = resolve_text_provider("DEEPSEEK/deepseek-v4-pro", {"DEEPSEEK_API_KEY": "fake"})
    monkeypatch.setattr(main, "resolve_text_provider", lambda model=None: provider)
    monkeypatch.setattr(main, "post_chat_completion", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call")))

    response = client.post("/api/ai/solve", data={
        "content": "题干", "stream": "false", "image_paths": json.dumps([url]),
    })
    assert response.status_code == 400
    assert "不支持图片输入" in response.json()["detail"]


def test_solve_rejects_missing_or_unsafe_image(client):
    import main

    client.headers["X-Local-Token"] = main.LOCAL_TOKEN
    response = client.post("/api/ai/solve", data={
        "content": "题干", "stream": "false",
        "image_paths": json.dumps(["/static/uploads/../starhope.db"]),
    })
    assert response.status_code == 400
    assert "附图不可用" in response.json()["detail"]


def test_solve_frontend_sends_image_paths():
    source = Path("static/js/ocr.js").read_text(encoding="utf-8")
    assert "image_paths" in source and "StarHopeImageAssets.collect" in source
    cards = Path("static/js/import.js").read_text(encoding="utf-8")
    assert "parsedSolveImagePaths" in cards
    assert cards.count("image_paths', JSON.stringify(parsedSolveImagePaths") == 2
