from odoo import models, fields, api
from markupsafe import Markup
import json
import requests
import logging
import time


_logger = logging.getLogger(__name__)


class AugmontApiRetry(models.Model):
    _name = "augmont.api.retry"
    _description = "Augmont Failed API Calls"
    _order = "create_date desc"

    order_id = fields.Many2one("sale.order", string="Sale Order")
    order_line_id = fields.Many2one("sale.order.line", string="Sale Order Line")
    order_number = fields.Char(string="Order Number")

    sdk_augmont_number = fields.Char(string="Augmont Number")
    updated_on = fields.Datetime(string="Updated On")

    payload_json = fields.Text(string="Payload JSON")
    headers_json = fields.Text(string="Headers JSON")

    old_status = fields.Char(string="Old Status")
    new_status = fields.Char(string="New Status")

    error_message = fields.Text(string="Error Message")
    status_code = fields.Char(string="HTTP Code")

    state = fields.Selection([
        ("pending", "Pending"),
        ("success", "Success"),
        ("failed", "Failed Permanently"),
    ], default="pending")

    # ─────────────────────────────────────────────────────────────────────────
    # CRON ENTRY POINT — retry_api_call()
    # ─────────────────────────────────────────────────────────────────────────
    # PURPOSE: Retry API calls that failed ONLY because the website was temporarily unreachable (network errors, timeouts, 5xx server errors).
    # ─────────────────────────────────────────────────────────────────────────

    # Status codes that indicate a TRANSIENT (retryable) failure:
    RETRYABLE_CODES = frozenset([
        'EXCEPTION',   # Connection error / timeout stored as text
        '500', '502', '503', '504',
        '',            # Empty = original call never got a response
        'None',
    ])

    # Error substrings that mark a PERMANENT / invalid-transition failure:
    PERMANENT_ERROR_PHRASES = (
        'Status transition',
        'not allowed',
        'is not allowed',
        'invalid status',
        'Invalid status',
    )

    def _is_permanent_failure(self, error_message, status_code):

        if error_message:
            for phrase in self.PERMANENT_ERROR_PHRASES:
                if phrase in error_message:
                    return True
        # HTTP 4xx (business rejections), except 408 Request Timeout
        try:
            code = int(status_code)
            if 400 <= code < 500 and code != 408:
                return True
        except (TypeError, ValueError):
            pass
        return False

    def retry_api_call(self):
        _logger.info("=" * 60)
        _logger.info("[CRON] ▶ Starting Augmont API Retry Job")

        Param = self.env['ir.config_parameter'].sudo()
        base_url = Param.get_param('base_augmont_url')
        api_url = f"{base_url}/api/v1/odoo/orderStatusUpdate"

        pending_records = self.search([('state', '=', 'pending')], order='id asc')
        _logger.info("[CRON] Pending retry records found: %d", len(pending_records))

        if not pending_records:
            _logger.info("[CRON] Nothing to retry — exiting early.")
            _logger.info("=" * 60)
            return

        # ── Website availability probe ────────────────────────────────────────
        website_reachable = True
        try:
            probe = requests.head(base_url, timeout=5)
            website_reachable = True
            _logger.info(
                "[CRON] Website probe → HTTP %s (website is UP — will only retry transient failures)",
                probe.status_code
            )
        except requests.exceptions.RequestException as probe_err:
            website_reachable = False
            _logger.warning(
                "[CRON] Website probe FAILED → website appears DOWN (%s) — will retry ALL pending",
                probe_err
            )

        processed = skipped_permanent = skipped_website_up = 0

        for rec in pending_records:
            _logger.info(
                "[CRON] ── Examining Record %d | Line: %s | %s → %s | Code: %s",
                rec.id, rec.order_number, rec.old_status, rec.new_status,
                rec.status_code
            )

            # ── Step 1: Mark permanent failures immediately ───────────────────
            if self._is_permanent_failure(rec.error_message, str(rec.status_code or '')):
                rec.state = "failed"
                rec.updated_on = fields.Datetime.now()
                skipped_permanent += 1
                _logger.warning(
                    "[CRON] Record %d → PERMANENTLY FAILED (invalid transition or 4xx). "
                    "Marking as 'Failed Permanently'. Error: %s",
                    rec.id, (rec.error_message or '')[:200]
                )

                err_text = rec.error_message or ""
                is_same_status = (
                    "Status transition from" in err_text
                    and "is not allowed" in err_text
                    and rec.new_status
                    and f"from '{rec.new_status}' to '{rec.new_status}'" in err_text
                )
                is_db_to_db = (
                    rec.new_status == "Diamond Booked"
                    and (not rec.old_status or rec.old_status == "Diamond Booked")
                )
                should_suppress = is_same_status or is_db_to_db
                if not should_suppress and rec.order_id:
                    rec.order_id.message_post(
                        body=Markup(
                            f"<p style='color:orange;'><b>Augmont Retry: Permanently Failed</b></p>"
                            f"<p>Record {rec.id} | Line: {rec.order_number}</p>"
                            f"<p>Transition: {rec.old_status} → {rec.new_status}</p>"
                            f"<p>Reason: {rec.error_message}</p>"
                            f"<p><i>This will not be retried again.</i></p>"
                        )
                    )
                elif should_suppress:
                    reason = "same-status" if is_same_status else "Diamond Booked → Diamond Booked initial echo"
                    _logger.info(
                        "[CRON] Record %d: suppressed chatter — %s",
                        rec.id, reason
                    )
                continue

            # ── Step 2: If website is UP, skip non-transient failures ─────────
            if website_reachable and str(rec.status_code or '') not in self.RETRYABLE_CODES:
                _logger.info(
                    "[CRON] Record %d SKIPPED — website is UP but error code '%s' "
                    "is not transient (not a network/timeout issue).",
                    rec.id, rec.status_code
                )
                skipped_website_up += 1
                continue

            # ── Step 3: Retry ─────────────────────────────────────────────────
            try:
                payload = json.loads(rec.payload_json)
                headers = json.loads(rec.headers_json)

                _logger.info(
                    "[CRON] Retrying Record %d | URL: %s | Payload: %s",
                    rec.id, api_url, json.dumps(payload)
                )

                response = requests.post(api_url, json=payload, headers=headers, timeout=30)

                _logger.info(
                    "[CRON] Record %d | HTTP %s | Response: %s",
                    rec.id, response.status_code,
                    response.text[:300]
                )

                if response.status_code == 200:
                    rec.status_code = "200"
                    rec.state = "success"
                    rec.updated_on = fields.Datetime.now()
                    processed += 1
                    _logger.info("[CRON] Record %d → SUCCESS ✅", rec.id)
                    if rec.order_id:
                        rec.order_id.message_post(
                            body=Markup(
                                f"<p style='color:green;'><b>Augmont Retry SUCCESS ✅</b></p>"
                                f"<p>Record {rec.id} | Line: {rec.order_number}</p>"
                                f"<p>{rec.old_status} → {rec.new_status}</p>"
                                f"<p>{response.text}</p>"
                            )
                        )

                else:
                    rec.error_message = response.text
                    rec.status_code = str(response.status_code)
                    rec.updated_on = fields.Datetime.now()

                    if self._is_permanent_failure(response.text, str(response.status_code)):
                        rec.state = "failed"
                        skipped_permanent += 1
                        _logger.warning(
                            "[CRON] Record %d → PERMANENTLY FAILED after retry: %s",
                            rec.id, response.text[:200]
                        )
                    else:
                        rec.state = "pending"
                        _logger.warning(
                            "[CRON] Record %d still PENDING | HTTP %s | Will retry next run.",
                            rec.id, response.status_code
                        )

                time.sleep(0.5)

            except requests.exceptions.ConnectionError as conn_err:
                rec.error_message = f"Connection error: {conn_err}"
                rec.status_code = "EXCEPTION"
                rec.updated_on = fields.Datetime.now()
                rec.state = "pending"
                _logger.warning("[CRON] Record %d — ConnectionError: %s", rec.id, conn_err)

            except requests.exceptions.Timeout as timeout_err:
                rec.error_message = f"Timeout: {timeout_err}"
                rec.status_code = "EXCEPTION"
                rec.updated_on = fields.Datetime.now()
                rec.state = "pending"
                _logger.warning("[CRON] Record %d — Timeout: %s", rec.id, timeout_err)

            except Exception as e:
                rec.error_message = str(e)
                rec.status_code = "EXCEPTION"
                rec.updated_on = fields.Datetime.now()
                rec.state = "pending"
                _logger.exception("[CRON] Record %d — unexpected exception: %s", rec.id, e)

        _logger.info(
            "[CRON] ◀ Retry Job Complete | "
            "✅ Succeeded: %d | ❌ Permanent failures: %d | ⏭ Skipped (website up): %d",
            processed, skipped_permanent, skipped_website_up
        )
        _logger.info("=" * 60)
