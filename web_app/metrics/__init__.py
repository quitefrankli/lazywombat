import flask
import flask_login
import logging

from flask import render_template, Blueprint
from typing import * # type: ignore
from datetime import datetime

from web_app.helpers import limiter, cur_user, from_req
from web_app.users import User
from web_app.metrics.app_data import Metric, DataPoint
from web_app.metrics.data_interface import DataInterface
from web_app.metrics.visualiser import plot_metric


metrics_api = Blueprint(
    'metrics_api', 
    __name__, 
    template_folder='templates',
    static_folder='static',
    url_prefix='/metrics')

@metrics_api.context_processor
def inject_app_name():
    return dict(app_name='Metrics')

def get_default_redirect():
    return flask.redirect(flask.url_for('.get_metrics'))

@metrics_api.route('/', methods=['GET'])
@flask_login.login_required
@limiter.limit("2 per second")
def get_metrics():
    tld = DataInterface().load_data(cur_user())
    metrics = list(tld.metrics.values())
    metrics.sort(key=lambda x: x.id)
    for metric in metrics:
        metric.data.sort(key=lambda x: x.date, reverse=True)

    return render_template('metrics_page.html', metrics=metrics)

@metrics_api.route('/new', methods=['POST'])
@flask_login.login_required
@limiter.limit("2 per second")
def new_metric():
    name = from_req('name')
    unit = from_req('units')
    description = from_req('description')

    if not name:
        flask.flash('Metric name cannot be empty', category='error')
        return get_default_redirect()

    data_interface = DataInterface()
    tld = data_interface.load_data(cur_user())
    
    metric_id = 0 if not tld.metrics else max(tld.metrics.keys()) + 1
    tld.metrics[metric_id] = Metric(id=metric_id, 
                                    name=name, 
                                    data=[], 
                                    unit=unit, 
                                    description=description,
                                    creation_date=datetime.now())
    data_interface.save_data(tld, cur_user())
    
    return get_default_redirect()

@metrics_api.route('/delete', methods=['GET'])
@flask_login.login_required
@limiter.limit("2 per second")
def delete_metric():
    metric_id = int(from_req('metric_id'))
    tld = DataInterface().load_data(cur_user())
    tld.metrics.pop(metric_id)
    DataInterface().save_data(tld, cur_user())

    return get_default_redirect()

@metrics_api.route('/edit', methods=['POST'])
@flask_login.login_required
@limiter.limit("2 per second")
def edit_metric():
    metric_id = int(from_req('metric_id'))
    name = from_req('name')

    if not name:
        flask.flash('Metric name cannot be empty', category='error')
        return get_default_redirect()

    unit = from_req('units')
    description = from_req('description')

    tld = DataInterface().load_data(cur_user())
    metric = tld.metrics[metric_id]
    metric.name = name
    metric.unit = unit
    metric.description = description
    DataInterface().save_data(tld, cur_user())

    return get_default_redirect()

@metrics_api.route('/log', methods=['POST'])
@flask_login.login_required
@limiter.limit("2 per second")
def log_metric():
    metric_id = int(from_req('metric_id'))
    try:
        value = float(from_req('value'))
    except ValueError:
        flask.flash('Value must be a number', category='error')
        return get_default_redirect()

    tld = DataInterface().load_data(cur_user())
    metric = tld.metrics[metric_id]
    metric.data.append(DataPoint(date=datetime.now(), value=value))
    DataInterface().save_data(tld, cur_user())

    return get_default_redirect()

@metrics_api.route('/visualise/<int:metric_id>', methods=['GET'])
@flask_login.login_required
@limiter.limit("1 per second")
def visualise_metric(metric_id: int):
    tld = DataInterface().load_data(cur_user())
    metric = tld.metrics[metric_id]

    try:
        embeddable_plotly_html = plot_metric(metric)

        return render_template('metric_plot.html', plot=embeddable_plotly_html)
    except Exception as e:
        logging.error(f"Failed to visualise metric {metric_id}: {e}")
        flask.flash('Failed to visualise metric', category='error')

        return get_default_redirect()
