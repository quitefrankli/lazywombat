import flask
import logging

from flask import render_template, Blueprint
from typing import * # type: ignore
from datetime import datetime

from web_app.helpers import limiter, cur_user, from_req, register_app_name, require_login_blueprint
from web_app.users import User
from web_app.metrics.app_data import Metric, DataPoint
from web_app.metrics.data_interface import DataInterface
from web_app.metrics.visualiser import plot_metric
from web_app.logging_utils import log_event


metrics_api = Blueprint(
    'metrics_api', 
    __name__, 
    template_folder='templates',
    static_folder='static',
    url_prefix='/metrics')


require_login_blueprint(metrics_api)
register_app_name(metrics_api, 'Metrics')

def get_default_redirect():
    return flask.redirect(flask.url_for('.get_metrics'))

@metrics_api.route('/', methods=['GET'])
@limiter.limit("2 per second")
def get_metrics():
    tld = DataInterface().load_data(cur_user())
    metrics = list(tld.metrics.values())
    # Sort by last_modified (most recent first), similar to todoist
    metrics.sort(key=lambda x: x.last_modified.timestamp(), reverse=True)
    for metric in metrics:
        metric.data.sort(key=lambda x: x.date, reverse=True)

    return render_template('metrics_page.html', metrics=metrics)

@metrics_api.route('/new', methods=['POST'])
@limiter.limit("2 per second")
def new_metric():
    name = from_req('name')
    unit = from_req('units')
    description = from_req('description')

    if not name:
        log_event(
            "metrics",
            "metric.create_rejected",
            level=logging.WARNING,
            reason="empty_name",
        )
        flask.flash('Metric name cannot be empty', category='error')
        return get_default_redirect()

    with DataInterface().edit_data(cur_user()) as tld:
        metric_id = 0 if not tld.metrics else max(tld.metrics.keys()) + 1
        tld.metrics[metric_id] = Metric(id=metric_id,
                                        name=name,
                                        data=[],
                                        unit=unit,
                                        description=description,
                                        creation_date=datetime.now())

    log_event("metrics", "metric.created", metric_id=metric_id)
    return get_default_redirect()

@metrics_api.route('/delete', methods=['GET'])
@limiter.limit("2 per second")
def delete_metric():
    metric_id = int(from_req('metric_id'))
    with DataInterface().edit_data(cur_user()) as tld:
        tld.metrics.pop(metric_id)

    log_event("metrics", "metric.deleted", metric_id=metric_id)
    return get_default_redirect()

@metrics_api.route('/edit', methods=['POST'])
@limiter.limit("2 per second")
def edit_metric():
    metric_id = int(from_req('metric_id'))
    name = from_req('name')

    if not name:
        log_event(
            "metrics",
            "metric.update_rejected",
            level=logging.WARNING,
            metric_id=metric_id,
            reason="empty_name",
        )
        flask.flash('Metric name cannot be empty', category='error')
        return get_default_redirect()

    unit = from_req('units')
    description = from_req('description')

    with DataInterface().edit_data(cur_user()) as tld:
        metric = tld.metrics[metric_id]
        metric.name = name
        metric.unit = unit
        metric.description = description
        metric.last_modified = datetime.now()

    log_event("metrics", "metric.updated", metric_id=metric_id)
    return get_default_redirect()

@metrics_api.route('/log', methods=['POST'])
@limiter.limit("2 per second")
def log_metric():
    metric_id = int(from_req('metric_id'))
    try:
        value = float(from_req('value'))
    except ValueError:
        log_event(
            "metrics",
            "metric.log_rejected",
            level=logging.WARNING,
            metric_id=metric_id,
            reason="invalid_value",
        )
        flask.flash('Value must be a number', category='error')
        return get_default_redirect()

    with DataInterface().edit_data(cur_user()) as tld:
        metric = tld.metrics[metric_id]
        metric.data.append(DataPoint(date=datetime.now(), value=value))
        metric.last_modified = datetime.now()

    log_event("metrics", "metric.logged", metric_id=metric_id)
    return get_default_redirect()

@metrics_api.route('/visualise/<int:metric_id>', methods=['GET'])
@limiter.limit("1 per second")
def visualise_metric(metric_id: int):
    tld = DataInterface().load_data(cur_user())
    metric = tld.metrics[metric_id]

    try:
        embeddable_plotly_html = plot_metric(metric)

        return render_template('metric_plot.html', plot=embeddable_plotly_html)
    except Exception as e:
        log_event(
            "metrics",
            "metric.visualisation_failed",
            level=logging.ERROR,
            metric_id=metric_id,
            exc_info=e,
            error_type=type(e).__name__,
        )
        flask.flash('Failed to visualise metric', category='error')

        return get_default_redirect()
