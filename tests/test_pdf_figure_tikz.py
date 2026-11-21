"""Per-figure PDF redraw returns TikZ plus a compiled preview; crops stay fallback."""
from pathlib import Path

from PIL import Image


def _upload_asset(root: Path, name: str) -> str:
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 24), "white").save(root / name)
    return f"/static/{root.name}/{name}"


def test_redraw_returns_tikz_and_preview(client, monkeypatch):
    import main
    from starhope.tikz_label_layout import TikzPreview

    client.headers["X-Local-Token"] = main.LOCAL_TOKEN
    root = Path(main.UPLOAD_DIR)
    url = _upload_asset(root, "pdf-figure-tikz.png")

    monkeypatch.setattr(
        main, "draw_tikz_via_high_model",
        lambda *args, **kwargs: "\\draw (0,0)--(1,1);",
    )
    monkeypatch.setattr(
        main, "compile_tikz_with_label_review",
        lambda code, **kwargs: TikzPreview("/static/uploads/tikz-preview.png", code),
    )

    response = client.post("/api/ai/redraw_figure_tikz", data={
        "image_path": url, "content": "已知三角形ABC", "field": "content",
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tikz_code"] == "\\draw (0,0)--(1,1);"
    assert payload["preview_image_path"] == "/static/uploads/tikz-preview.png"
    assert payload["reference_image_path"] == url
    assert payload["field"] == "content"


def test_redraw_collision_keeps_original_crop(client, monkeypatch):
    import main
    from starhope.tikz_label_layout import TikzPreview

    client.headers["X-Local-Token"] = main.LOCAL_TOKEN
    root = Path(main.UPLOAD_DIR)
    url = _upload_asset(root, "pdf-figure-collision.png")
    monkeypatch.setattr(main, "draw_tikz_via_high_model", lambda *args, **kwargs: "\\draw (0,0)--(1,1);")
    monkeypatch.setattr(
        main, "compile_tikz_with_label_review",
        lambda code, **kwargs: TikzPreview("/static/uploads/tikz-collision.png", code, ("A",)),
    )
    response = client.post("/api/ai/redraw_figure_tikz", data={"image_path": url})
    assert response.status_code == 409
    assert "点名" in response.json()["detail"]


def test_redraw_rejects_unsafe_paths_and_unknown_fields(client, monkeypatch):
    import main

    client.headers["X-Local-Token"] = main.LOCAL_TOKEN
    monkeypatch.setattr(main, "draw_tikz_via_high_model", lambda *args, **kwargs: "\\draw (0,0)--(1,1);")
    assert client.post("/api/ai/redraw_figure_tikz", data={
        "image_path": "/static/uploads/../starhope.db",
    }).status_code == 404
    root = Path(main.UPLOAD_DIR)
    url = _upload_asset(root, "pdf-figure-field.png")
    assert client.post("/api/ai/redraw_figure_tikz", data={
        "image_path": url, "field": "review",
    }).status_code == 400


def test_pdf_cards_expose_redraw_button_and_save_assets():
    source = Path("static/js/import.js").read_text(encoding="utf-8")
    assert "/api/ai/redraw_figure_tikz" in source
    assert "重绘 TikZ" in source
    assert "confirmTikzRedraw" in source
    assert "answer_tikz_assets" in source
    assert "content_tikz_assets" in source
