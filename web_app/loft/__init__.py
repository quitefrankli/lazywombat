import base64
import gzip
import json
import logging
import zipfile
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO

import flask_login
from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge

from web_app.app import csrf
from web_app.config import ConfigManager
from web_app.errors import APIError
from web_app.loft.data_interface import DataInterface, slugify
from web_app.helpers import cur_user, limiter, parse_request, register_app_name
from web_app.logging_utils import log_event

loft_api = Blueprint(
    'loft',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/loft'
)


register_app_name(loft_api, 'Loft')


@loft_api.context_processor
def inject_loft_config():
    return {"loft_config": ConfigManager().loft}


def _gallery_request_is_too_large() -> bool:
    request.max_content_length = (
        ConfigManager().loft.gallery_request_max_bytes
    )
    content_length = request.content_length
    return bool(
        content_length is not None
        and content_length > ConfigManager().loft.gallery_request_max_bytes
    )


@loft_api.errorhandler(RequestEntityTooLarge)
def gallery_request_too_large(error):
    message = "Selected media exceeds the upload request size limit"
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"error": message}), 413
    return message, 413


def _require_post_owner(project: str, post: str):
    """Abort with 403 unless the current user can edit this post."""
    if not flask_login.current_user.is_authenticated:
        abort(403)
    if not DataInterface().user_can_edit(flask_login.current_user, project, post):
        abort(403)


def owner_or_admin(func):
    @wraps(func)
    def wrapper(project, post, *args, **kwargs):
        _require_post_owner(project, post)
        return func(project, post, *args, **kwargs)
    return wrapper


@loft_api.route('/')
def index():
    posts_by_project = DataInterface().get_posts_by_project(flask_login.current_user)
    return render_template("loft_index.html", posts_by_project=posts_by_project)


@loft_api.route('/new', methods=['GET', 'POST'])
@flask_login.login_required
def new_post():
    di = DataInterface()
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def fail(message: str):
        if wants_json:
            return jsonify({"error": message}), 400
        flash(message, "error")
        return redirect(url_for('.new_post'))

    if request.method == 'POST':
        if _gallery_request_is_too_large():
            message = "Selected media exceeds the upload request size limit"
            if wants_json:
                return jsonify({"error": message}), 413
            flash(message, "error")
            return redirect(url_for('.new_post'))

        project_input = (request.form.get('project_existing') or request.form.get('project_new') or '').strip()
        template = (request.form.get('template') or '').strip()
        title = (request.form.get('title') or '').strip()

        if not project_input:
            return fail("Topic is required")
        if template not in ('markdown', 'gallery'):
            return fail("Pick a template")
        if not title:
            return fail("Title is required")

        user = cur_user()
        try:
            if template == 'markdown':
                source_md = request.form.get('source_md') or ''
                project_slug, post_slug = di.create_markdown_post(user, project_input, title, source_md)
            else:
                description = (request.form.get('description') or '').strip()
                project_slug, post_slug = di.create_gallery_post(user, project_input, title, description)
                files = [f for f in request.files.getlist('files') if f and f.filename]
                if files:
                    try:
                        di.add_gallery_images(user, project_slug, post_slug, files)
                    except (APIError, OSError) as error:
                        # Roll back the empty post so create-with-images is atomic.
                        di.delete_post(project_slug, post_slug)
                        if isinstance(error, APIError):
                            raise
                        raise APIError(
                            "Could not store gallery upload"
                        ) from error
        except APIError as e:
            return fail(str(e))

        log_event(
            "loft",
            "loft.post_created",
            user=user,
            project=project_slug,
            post=post_slug,
            template=template,
        )
        if wants_json:
            return jsonify({"redirect_url": url_for('.view_post', project=project_slug, post=post_slug)})
        return redirect(url_for('.view_post', project=project_slug, post=post_slug))

    return render_template(
        "loft_new.html",
        posts_by_project=di.get_posts_by_project(flask_login.current_user),
    )


@loft_api.route('/<project>/')
def view_project(project: str):
    posts_by_project = DataInterface().get_posts_by_project(flask_login.current_user)
    project_obj = next((p for p in posts_by_project if p.name == project), None)
    if project_obj and project_obj.posts:
        return redirect(url_for('.view_post', project=project, post=project_obj.posts[0]))
    return redirect(url_for('.index', open=project))


@loft_api.route('/<project>/<post>/')
def view_post(project: str, post: str):
    di = DataInterface()
    if not di.user_can_view(flask_login.current_user, project, post):
        abort(404)
    posts_by_project = di.get_posts_by_project(flask_login.current_user)
    try:
        post_content = di.get_post_content(project, post)
    except FileNotFoundError:
        abort(404)
    meta = di.get_post_meta(project, post)
    can_edit = di.user_can_edit(flask_login.current_user, project, post)
    return render_template(
        "loft_post.html",
        project_name=project,
        post_name=post,
        posts_by_project=posts_by_project,
        post_content=post_content,
        meta=meta,
        can_edit=can_edit,
    )


@loft_api.route('/<project>/<post>/edit', methods=['GET', 'POST'])
@owner_or_admin
def edit_post(project: str, post: str):
    di = DataInterface()
    meta = di.get_post_meta(project, post)
    template = meta.type.value
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method == 'POST':
        if _gallery_request_is_too_large():
            message = "Selected media exceeds the upload request size limit"
            if wants_json:
                return jsonify({"error": message}), 413
            flash(message, "error")
            return redirect(
                url_for('.edit_post', project=project, post=post)
            )

        title = (request.form.get('title') or '').strip()
        try:
            if template == 'markdown':
                source_md = request.form.get('source_md') or ''
                di.update_markdown_post(project, post, title, source_md)
            elif template == 'gallery':
                description = (request.form.get('description') or '').strip()
                media_order = None
                raw_media_order = request.form.get('media_order')
                if raw_media_order is not None:
                    try:
                        media_order = json.loads(raw_media_order)
                    except (json.JSONDecodeError, TypeError) as e:
                        raise APIError("Media order is invalid") from e
                    if not isinstance(media_order, list) or not all(
                        isinstance(filename, str) for filename in media_order
                    ):
                        raise APIError("Media order is invalid")
                di.update_gallery_meta(project, post, title, description, media_order)
                files = [f for f in request.files.getlist('files') if f and f.filename]
                if files:
                    di.add_gallery_images(cur_user(), project, post, files)
            else:
                # raw/legacy posts: meta-only updates aren't supported
                if wants_json:
                    return jsonify({"error": "This post type can't be edited in the browser."}), 400
                flash("This post type can't be edited in the browser.", "error")
                return redirect(url_for('.view_post', project=project, post=post))
        except APIError as e:
            if wants_json:
                return jsonify({"error": str(e)}), 400
            flash(str(e), "error")
            return redirect(url_for('.edit_post', project=project, post=post))
        log_event(
            "loft",
            "loft.post_updated",
            project=project,
            post=post,
            template=template,
        )
        flash("Saved.", "success")
        if wants_json:
            return jsonify({"redirect_url": url_for('.view_post', project=project, post=post)})
        return redirect(url_for('.view_post', project=project, post=post))

    posts_by_project = di.get_posts_by_project(flask_login.current_user)
    if template == 'markdown':
        return render_template(
            "loft_edit_markdown.html",
            project_name=project,
            post_name=post,
            meta=meta,
            source_md=di.get_markdown_source(project, post),
            posts_by_project=posts_by_project,
        )
    if template == 'gallery':
        return render_template(
            "loft_edit_gallery.html",
            project_name=project,
            post_name=post,
            meta=meta,
            gallery=di.get_gallery(project, post),
            posts_by_project=posts_by_project,
        )
    flash("This post type can't be edited in the browser.", "error")
    return redirect(url_for('.view_post', project=project, post=post))


@loft_api.route('/<project>/<post>/images', methods=['POST'])
@owner_or_admin
def add_gallery_images(project: str, post: str):
    if _gallery_request_is_too_large():
        flash("Selected media exceeds the upload request size limit", "error")
        return redirect(url_for('.edit_post', project=project, post=post))

    di = DataInterface()
    files = request.files.getlist('files')
    user = cur_user()
    try:
        n = di.add_gallery_media(user, project, post, files)
    except APIError as e:
        flash(str(e), "error")
        return redirect(url_for('.edit_post', project=project, post=post))
    log_event(
        "loft",
        "loft.gallery_media_added",
        user=user,
        project=project,
        post=post,
        count=n,
    )
    flash(f"Uploaded {n} media item{'s' if n != 1 else ''}.", "success")
    return redirect(url_for('.edit_post', project=project, post=post))


@loft_api.route('/<project>/<post>/images/<path:filename>/delete', methods=['POST'])
@owner_or_admin
def delete_gallery_image(project: str, post: str, filename: str):
    di = DataInterface()
    try:
        di.delete_gallery_media(project, post, filename)
    except APIError as e:
        flash(str(e), "error")
        return redirect(url_for('.edit_post', project=project, post=post))
    log_event(
        "loft",
        "loft.gallery_media_deleted",
        project=project,
        post=post,
        filename=filename,
    )
    return redirect(url_for('.edit_post', project=project, post=post))


@loft_api.route('/<project>/<post>/delete', methods=['POST'])
@owner_or_admin
def delete_post(project: str, post: str):
    di = DataInterface()
    meta = di.get_post_meta(project, post)
    di.delete_post(project, post)
    log_event(
        "loft",
        "loft.post_deleted",
        project=project,
        post=post,
        owner=meta.owner,
        template=meta.type.value,
    )
    flash("Post deleted.", "success")
    return redirect(url_for('.index'))


@loft_api.route('/api/upload_post', methods=['POST'])
@csrf.exempt
def upload_post():
    try:
        request_body = parse_request(require_login=True, require_admin=True)
    except APIError as e:
        return jsonify({"error": str(e)}), 400

    project = request_body.get("project")
    post_name = request_body.get("post_name")
    data = request_body.get("data")

    if not project or not post_name or not data:
        return jsonify({"error": "Missing required fields: project, post_name, data"}), 400

    try:
        decoded_data = base64.b64decode(data)
        plain_data = gzip.decompress(decoded_data)
    except Exception as e:
        return jsonify({"error": f"Failed to decode data: {e}"}), 400

    data_interface = DataInterface()
    try:
        post_dir = data_interface._post_dir(project, post_name)
    except APIError as e:
        return jsonify({"error": str(e)}), 400
    post_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(BytesIO(plain_data), 'r') as zf:
            for member in zf.namelist():
                target = (post_dir / member).resolve()
                if not target.is_relative_to(post_dir.resolve()):
                    return jsonify({"error": "Zip contains path traversal"}), 400
            zf.extractall(post_dir)
    except zipfile.BadZipFile:
        return jsonify({"error": "Invalid zip data"}), 400

    forced_date = request_body.get("date")
    date = forced_date if forced_date else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    actor = request_body.get("username") or "<api>"
    data_interface.atomic_delete(post_dir / "meta.json")
    data_interface.register_raw_post(
        project,
        post_name,
        request_body.get("title") or post_name,
        actor,
        date,
    )
    log_event(
        "loft",
        "loft.raw_post_uploaded",
        user=actor,
        project=project,
        post=post_name,
    )
    return jsonify({"success": True, "message": f"Post {project}/{post_name} uploaded"}), 200


@loft_api.route('/<project>/<post>/<path:filename>')
@limiter.limit("20/second")
def post_asset(project: str, post: str, filename: str):
    data_interface = DataInterface()
    if not data_interface.user_can_view(flask_login.current_user, project, post):
        abort(404)
    asset_path = data_interface.get_asset_path(project, post, filename)
    if not asset_path or not asset_path.exists():
        abort(404)
    return send_file(asset_path)
