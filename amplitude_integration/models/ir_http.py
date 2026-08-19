# -*- coding: utf-8 -*-
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        """Expose the (public) Amplitude frontend settings to the web client.

        Only the API key and feature flags are sent; the secret key never
        leaves the server.
        """
        result = super().session_info()
        config = self.env['amplitude.config'].get_config()
        if config:
            result['amplitude'] = config.frontend_values()
        else:
            result['amplitude'] = {'enabled': False}
        return result
