# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""Formulario público de alta de contactos (feature-formulario-alta).

Independiente de insa_reparto_api.py (auth="public" ahí es la credencial
propia por ruta; acá es un formulario de website sin autenticación de
ningún tipo, pensado para visitantes anónimos del sitio). Crea leads/
contactos nuevos vía sudo() desde el controlador — no le dar permisos ACL
públicos de escritura a res.partner ni a insa.city/insa.street, este
controlador es el único punto que necesita ese privilegio.

No marca customer_rank: un alta acá es un contacto nuevo, no un cliente de
reparto confirmado — no aparece en el Buscador (Paso 2.6) ni puede recibir
visitas hasta que INSA lo convierta en cliente por el circuito normal de
Odoo (decisión explícita, no un olvido)."""
from odoo import http
from odoo.http import request


class InsaRegistrationController(http.Controller):

    REQUIRED_FIELDS = {
        'name': 'Nombre y Apellido o Razón Social',
        'mobile_code': 'Código de celular',
        'mobile_number': 'Número de celular',
        'insa_city_id': 'Ciudad o Barrio',
        'insa_street_id': 'Calle',
        'insa_street_number': 'Número',
    }

    @http.route('/formulario-alta-insa', type='http', auth='public', website=True, sitemap=False)
    def formulario_alta(self, **kwargs):
        return request.render('insa_reparto.formulario_alta_insa_template', self._form_values())

    @http.route('/formulario-alta-insa/enviar', type='http', auth='public', website=True, methods=['POST'], csrf=True)
    def formulario_alta_submit(self, **post):
        errors = self._validate(post)
        if errors:
            values = self._form_values()
            values.update({'errors': errors, 'form_data': post})
            return request.render('insa_reparto.formulario_alta_insa_template', values)

        request.env['res.partner'].sudo().create(self._prepare_partner_values(post))
        return request.redirect('/formulario-alta-insa/gracias')

    @http.route('/formulario-alta-insa/gracias', type='http', auth='public', website=True, sitemap=False)
    def formulario_alta_gracias(self, **kwargs):
        return request.render('insa_reparto.formulario_alta_insa_gracias_template', {})

    def _form_values(self):
        return {
            'cities': request.env['insa.city'].sudo().search([]),
            'streets': request.env['insa.street'].sudo().search([]),
            'errors': {},
            'form_data': {},
        }

    def _validate(self, post):
        errors = {}
        for field, label in self.REQUIRED_FIELDS.items():
            if not post.get(field):
                errors[field] = '%s es obligatorio' % label
        return errors

    def _prepare_partner_values(self, post):
        mobile = ' '.join(filter(None, [
            (post.get('mobile_code') or '').strip(),
            (post.get('mobile_number') or '').strip(),
        ]))
        phone = ' '.join(filter(None, [
            (post.get('phone_code') or '').strip(),
            (post.get('phone_number') or '').strip(),
        ]))

        city = request.env['insa.city'].sudo().browse(int(post.get('insa_city_id')))
        street = request.env['insa.street'].sudo().browse(int(post.get('insa_street_id')))
        street_number = (post.get('insa_street_number') or '').strip()
        street_display = ('%s %s' % (street.name, street_number)).strip()

        return {
            'name': post.get('name'),
            'email': post.get('email') or False,
            'mobile': mobile or False,
            'phone': phone or False,
            'city': city.name,
            'street': street_display,
            'vat': post.get('vat') or False,
            'insa_city_id': city.id,
            'insa_street_id': street.id,
            'insa_street_number': street_number or False,
            'insa_floor': post.get('insa_floor') or False,
            'insa_apartment': post.get('insa_apartment') or False,
            'insa_tower': post.get('insa_tower') or False,
            'insa_block': post.get('insa_block') or False,
            'insa_lot': post.get('insa_lot') or False,
            'insa_address_notes': post.get('insa_address_notes') or False,
            'insa_tax_condition': post.get('insa_tax_condition') or 'consumidor_final',
            'insa_order_notes': post.get('insa_order_notes') or False,
        }
