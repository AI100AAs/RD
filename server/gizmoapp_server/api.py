from __future__ import annotations

import base64
import json
import math
import re
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from flask import Flask, Response, current_app, g, jsonify, request, stream_with_context
from werkzeug.exceptions import BadRequest, HTTPException, RequestEntityTooLarge, UnsupportedMediaType

from .capabilities import capability_payload
from .capabilities.audio import analyze_samples
from .capabilities.mapping import openstreetmap_config
from .capabilities.ml import run_kmeans, sklearn_status
from .capabilities.optimization import nearest_neighbor_route
from .capabilities.search import search_records
from .config import scoped_path
from .db import (
    database_readiness,
    fetch_room_creations,
    fetch_sample_nodes,
    get_db,
    insert_room_creation,
    insert_sample_node,
)
from .llm import CourseLLMError, ask_with_image
from .media import CourseMediaError, edit_image

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_RE = re.compile(r"^[a-z0-9-]{3,40}$")
MAX_LABEL_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2_000
MAX_SEARCH_QUERY_LENGTH = 200
MAX_ROOM_OPTION = 99
USER_ID_HEADERS = ("X-Gizmo-User-ID", "X-User-ID", "X-Forwarded-User")


def _health_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "serverTime": datetime.now(UTC).isoformat(),
    }


def _current_user_id() -> str:
    """Use the identity supplied by the hosting proxy, never browser state."""
    for header in USER_ID_HEADERS:
        value = request.headers.get(header, "").strip()
        if value:
            return value[:255]
    remote_user = request.environ.get("REMOTE_USER", "").strip()
    return remote_user[:255]


def _bootstrap_payload() -> dict[str, Any]:
    return {
        "app": {
            "name": current_app.config["APP_NAME"],
            "tagline": current_app.config["APP_TAGLINE"],
            "mode": "public",
            "shell": current_app.config["APP_SHELL"],
            "shellLabel": current_app.config["APP_SHELL_LABEL"],
        },
        "health": _health_payload(),
        "availableShells": current_app.config["AVAILABLE_SHELLS"],
    }


def _api_root() -> str:
    return scoped_path(current_app.config["URL_PREFIX"], "api").rstrip("/")


def _is_json_surface() -> bool:
    api_root = _api_root()
    return (
        request.path == api_root
        or request.path.startswith(f"{api_root}/")
        or request.path.endswith("/healthz")
        or request.path.endswith("/readyz")
        or request.path in {"/healthz", "/readyz"}
    )


def _error_response(message: str, status: int):
    return jsonify({"errors": [message], "requestId": getattr(g, "request_id", None)}), status


def _json_object() -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    if not request.is_json:
        return None, _error_response("Content-Type must be application/json", 415)
    try:
        payload = request.get_json(silent=False)
    except (BadRequest, UnsupportedMediaType):
        return None, _error_response("Request body must contain valid JSON", 400)
    if not isinstance(payload, dict):
        return None, _error_response("JSON request body must be an object", 400)
    return payload, None


def _finite_number(payload: dict[str, Any], key: str, default: float) -> float:
    value = float(payload.get(key, default))
    if not math.isfinite(value):
        raise ValueError(f"{key} must be finite")
    return value


def _normalize_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    raw_slug = payload.get("slug", "")
    raw_label = payload.get("label", "")
    raw_description = payload.get("description", "")
    raw_color = payload.get("accent_color", "#72d1c2")

    for name, value in (
        ("slug", raw_slug),
        ("label", raw_label),
        ("description", raw_description),
        ("accent_color", raw_color),
    ):
        if not isinstance(value, str):
            errors.append(f"{name} must be a string")

    cleaned = {
        "slug": raw_slug.strip() if isinstance(raw_slug, str) else "",
        "label": raw_label.strip() if isinstance(raw_label, str) else "",
        "description": raw_description.strip() if isinstance(raw_description, str) else "",
        "accent_color": raw_color.strip() if isinstance(raw_color, str) else "",
    }
    cleaned["description"] = cleaned["description"] or "Created through the sample API."

    if not SLUG_RE.fullmatch(cleaned["slug"]):
        errors.append("slug must be 3-40 characters of lowercase letters, digits, or hyphens")
    if len(cleaned["label"]) < 2 or len(cleaned["label"]) > MAX_LABEL_LENGTH:
        errors.append(f"label must be 2-{MAX_LABEL_LENGTH} characters")
    if len(cleaned["description"]) > MAX_DESCRIPTION_LENGTH:
        errors.append(f"description must be at most {MAX_DESCRIPTION_LENGTH} characters")
    if not HEX_COLOR_RE.fullmatch(cleaned["accent_color"]):
        errors.append("accent_color must be a 6-digit hex color like #72d1c2")

    try:
        cleaned["x"] = min(0.92, max(0.08, _finite_number(payload, "x", 0.5)))
        cleaned["y"] = min(0.92, max(0.08, _finite_number(payload, "y", 0.5)))
        cleaned["radius"] = min(0.2, max(0.06, _finite_number(payload, "radius", 0.11)))
    except (TypeError, ValueError, OverflowError):
        errors.append("x, y, and radius must be finite numbers")

    return cleaned, errors


def register_api_routes(app: Flask) -> None:
    prefix = app.config["URL_PREFIX"]
    enabled_features = frozenset(app.config["ENABLED_FEATURES"])

    @app.before_request
    def assign_request_id():
        g.request_id = secrets.token_hex(8)

    @app.after_request
    def harden_response(response):
        response.headers.setdefault("X-Request-ID", getattr(g, "request_id", ""))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_: RequestEntityTooLarge):
        if _is_json_surface():
            return _error_response("Request body is too large", 413)
        return "Request body is too large", 413

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        if _is_json_surface():
            return _error_response(error.description or error.name, error.code or 500)
        return error

    @app.errorhandler(Exception)
    def unexpected_error(error: Exception):
        current_app.logger.exception("Unhandled request error")
        if _is_json_surface():
            return _error_response("The server could not complete the request", 500)
        return "The server could not complete the request", 500

    @app.get(scoped_path(prefix, "healthz"))
    def healthz():
        return jsonify(_health_payload())

    @app.get(scoped_path(prefix, "readyz"))
    def readyz():
        ready, detail = database_readiness(current_app.config)
        return jsonify({"status": "ready" if ready else "not-ready", **detail}), 200 if ready else 503

    @app.get(scoped_path(prefix, "api/bootstrap"))
    def bootstrap():
        return jsonify(_bootstrap_payload())

    @app.get(scoped_path(prefix, "api/capabilities"))
    def capabilities():
        api_base = scoped_path(prefix, "api").rstrip("/")
        return jsonify(capability_payload(api_base, enabled_features))

    @app.post(scoped_path(prefix, "api/room/refine"))
    def room_refine():
        photo = request.files.get("photo")
        brief = request.form.get("brief", "")
        if photo is None or not photo.filename:
            return _error_response("Choose a room photo before refining", 400)
        if not isinstance(brief, str) or not brief.strip() or len(brief.strip()) > 1000:
            return _error_response("brief must be between 1 and 1,000 characters", 400)
        if photo.mimetype not in {"image/jpeg", "image/png", "image/webp"}:
            return _error_response("Photo must be a PNG, JPG, or WEBP image", 400)
        image_data = photo.read()
        if not image_data or len(image_data) > 8 * 1024 * 1024:
            return _error_response("Photo must be smaller than 8 MB", 400)
        try:
            prompt = ask_with_image(
                "You are an expert interior stylist. Turn the user's rough room redesign idea "
                "into one concise, vivid image-generation direction. Preserve their intent, "
                "use the uploaded room photo to ground your description in the existing space, "
                "and add tasteful material, lighting, layout, and photography details. Avoid "
                "mentioning AI or explaining your choices. Return only the final direction.\n\n"
                f"User idea: {brief.strip()}",
                image_data,
                photo.mimetype,
                max_tokens=500,
            ).strip()
        except CourseLLMError as exc:
            return _error_response(str(exc), 503)
        return jsonify({"prompt": prompt[:2000]})

    @app.get(scoped_path(prefix, "api/room/history"))
    def room_history():
        return jsonify({"creations": fetch_room_creations(get_db(), owner_id=_current_user_id())})

    @app.post(scoped_path(prefix, "api/room/generate"))
    def room_generate():
        photo = request.files.get("photo")
        prompt = request.form.get("prompt", "").strip()
        raw_start_option = request.form.get("start_option", "1").strip()
        if photo is None or not photo.filename:
            return _error_response("Choose a room photo before generating", 400)
        if not prompt or len(prompt) > 2000:
            return _error_response("prompt must be between 1 and 2,000 characters", 400)
        try:
            start_option = int(raw_start_option)
        except (TypeError, ValueError):
            return _error_response("start_option must be a whole number between 1 and 99", 400)
        if not 1 <= start_option <= MAX_ROOM_OPTION:
            return _error_response("start_option must be a whole number between 1 and 99", 400)
        if photo.mimetype not in {"image/jpeg", "image/png", "image/webp"}:
            return _error_response("Photo must be a PNG, JPG, or WEBP image", 400)
        image_data = photo.read()
        if not image_data or len(image_data) > 8 * 1024 * 1024:
            return _error_response("Photo must be smaller than 8 MB", 400)
        def events():
            def render_variation(seed: int):
                return edit_image(
                    prompt, image_data, model="instruct-pix2pix", strength=0.7, steps=20, seed=seed
                )

            completed = 0
            failures: list[str] = []
            # The course GPU is shared and may reject duplicate concurrent edits.
            # Keep the stream open while each bounded request is processed.
            for option_number in range(start_option, start_option + 2):
                seed = option_number * 101
                yield f"event: progress\ndata: {json.dumps({'option': option_number})}\n\n"
                try:
                    result = None
                    for attempt in range(2):
                        try:
                            result = render_variation(seed)
                            break
                        except CourseMediaError as exc:
                            if "worker is busy" not in str(exc).lower() or attempt:
                                raise
                            time.sleep(2)
                    if result is None:
                        raise CourseMediaError("The course-media worker did not return an image.")
                    encoded = base64.b64encode(result.data).decode("ascii")
                    insert_room_creation(
                        get_db(),
                        prompt=prompt,
                        option_number=option_number,
                        content_type=result.content_type,
                        image_data=encoded,
                        owner_id=_current_user_id(),
                    )
                    completed += 1
                    yield f"event: image\ndata: {json.dumps({'option': option_number, 'contentType': result.content_type, 'data': encoded})}\n\n"
                except CourseMediaError as exc:
                    message = str(exc)
                    failures.append(message)
                    yield f"event: variation-error\ndata: {json.dumps({'option': option_number, 'message': message})}\n\n"
            if completed:
                yield f"event: done\ndata: {json.dumps({'count': completed})}\n\n"
            else:
                message = failures[-1] if failures else "The course-media service returned no redesigns. Please try again."
                yield f"event: error\ndata: {json.dumps({'message': message})}\n\n"

        return Response(stream_with_context(events()), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if "search" in enabled_features:
        @app.get(scoped_path(prefix, "api/search"))
        def search():
            query = request.args.get("q", "")
            if len(query) > MAX_SEARCH_QUERY_LENGTH:
                return _error_response(f"q must be at most {MAX_SEARCH_QUERY_LENGTH} characters", 400)
            return jsonify(search_records(get_db(), query))

    if "mapping" in enabled_features:
        @app.get(scoped_path(prefix, "api/map/default"))
        def map_default():
            return jsonify(openstreetmap_config())

    if "machine-learning" in enabled_features:
        @app.get(scoped_path(prefix, "api/ml/status"))
        def ml_status():
            return jsonify(sklearn_status())

        @app.post(scoped_path(prefix, "api/ml/kmeans"))
        def ml_kmeans():
            payload, error = _json_object()
            if error:
                return error
            result, errors, status = run_kmeans(payload)
            if errors:
                return jsonify({"errors": errors, "requestId": g.request_id, **result}), status
            return jsonify(result)

    if "optimization" in enabled_features:
        @app.post(scoped_path(prefix, "api/optimize/route"))
        def optimize_route():
            payload, error = _json_object()
            if error:
                return error
            result, errors = nearest_neighbor_route(payload)
            if errors:
                return jsonify({"errors": errors, "requestId": g.request_id}), 400
            return jsonify(result)

    if "audio" in enabled_features:
        @app.post(scoped_path(prefix, "api/audio/analyze"))
        def audio_analyze():
            payload, error = _json_object()
            if error:
                return error
            result, errors = analyze_samples(payload)
            if errors:
                return jsonify({"errors": errors, "requestId": g.request_id}), 400
            return jsonify(result)

    if "sample-nodes" in enabled_features:
        @app.route(scoped_path(prefix, "api/sample-nodes"), methods=["GET", "POST"])
        def sample_nodes():
            connection = get_db()
            if request.method == "GET":
                return jsonify({"sampleNodes": fetch_sample_nodes(connection)})

            payload, error = _json_object()
            if error:
                return error
            cleaned, errors = _normalize_payload(payload)
            if errors:
                return jsonify({"errors": errors, "requestId": g.request_id}), 400

            try:
                record = insert_sample_node(connection, cleaned)
            except sqlite3.IntegrityError:
                return jsonify({"errors": ["slug already exists"], "requestId": g.request_id}), 409
            except sqlite3.OperationalError:
                current_app.logger.exception("Database write remained unavailable after retries")
                return _error_response("Database is temporarily busy; retry shortly", 503)

            return jsonify({"sampleNode": record}), 201

    @app.route(
        scoped_path(prefix, "api/<path:unmatched_path>"),
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    def unknown_api_route(unmatched_path: str):
        return _error_response(f"Unknown or disabled API route: {unmatched_path}", 404)
