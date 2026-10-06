# -*- coding: utf-8 -*-
"""Helpers shared by every model in the LGD operations flow (§3.8).

The responsible-user lookup and the activity helper were written on
``purchase.order.line`` when Logistics/QC was the only consumer. Inventory and
Dispatch need both on ``sale.order.line`` and later on ``lgd.dispatch``, and
two copies of the notification de-duplication would drift apart.

They are plain functions rather than an ``AbstractModel`` on purpose. Adding an
AbstractModel to ``purchase.order.line._inherit`` re-runs field setup on that
model and makes core's implicit ``move_dest_ids`` many2many collide with itself
("use the same table and columns"); adding a plain base class to the model is
refused outright by Odoo's MetaModel ("object layout differs"). These helpers
need no fields and no table, so functions keep the single copy of the logic
without touching the registry. Each model exposes a two-line delegate so call
sites stay ``self._lgd_notify(...)``.
"""

import logging

_logger = logging.getLogger(__name__)


def lgd_responsible_user(env, param_key, group_xmlid):
    """Login from the parameter, else the first active member of the group,
    else empty. Never raises — a missing responsible must not stop a stone
    being processed."""
    login = env['ir.config_parameter'].sudo().get_param(param_key)
    if login:
        user = env['res.users'].sudo().search([('login', '=', login)], limit=1)
        if user:
            return user
    group = env.ref(group_xmlid, raise_if_not_found=False)
    if group:
        user = env['res.users'].sudo().search(
            [('groups_id', 'in', group.id), ('active', '=', True)], limit=1)
        if user:
            return user
    _logger.warning(
        "No responsible user for %s / %s; activity not assigned.",
        param_key, group_xmlid)
    return env['res.users']


def lgd_notify(env, record, summary, note, user):
    """One open activity per (record, summary, user). Repeat calls are
    silently dropped, so a button pressed twice does not nag twice."""
    if not record:
        return
    domain = [
        ('res_model', '=', record._name),
        ('res_id', '=', record.id),
        ('summary', '=', summary),
    ]
    if user:
        domain.append(('user_id', '=', user.id))
    if env['mail.activity'].sudo().search_count(domain):
        return
    # sudo(): _mail_post_access defaults to 'write', so scheduling an activity
    # on a record the acting user can only read raises AccessError. This is a
    # system notification, not a user edit (§4.6.2).
    record.sudo().activity_schedule(
        'mail.mail_activity_data_todo', summary=summary, note=note,
        user_id=user.id if user else env.uid)
