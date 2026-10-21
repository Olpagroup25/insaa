# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""Revisión 07/10: abonos — migración del sistema viejo (Water Service,
pantalla "Clientes → Abonos"). Un cliente paga por mes una cantidad
incluida de uno o más productos; lo que consume de más ese mes (ciclo
calendario, 1° de cada mes) se vende aparte a un precio de excedente por
unidad, mediante una acción manual periódica (como "Vender excedentes al
abono" del sistema viejo) — no bloquea ni se dispara solo.

insa.abono.tipo es el catálogo de planes ("Abonos Tipos"); insa.abono es
el abono asignado a un cliente puntual, copiado de un tipo como plantilla
de partida pero editable por cliente (el sistema viejo mostraba "hasta 20"
en el nombre del plan con cantidad 10 en la línea real del cliente — la
plantilla es un punto de partida, no una restricción dura). Al
reemplazarlo, el abono viejo se desactiva (vigencia_hasta) y queda en el
historial, no se borra — mismo criterio que "Abonos inactivos".

Mientras el abono está activo, reemplaza al pedido habitual (4.3) para
los productos que cubre: la cantidad por visita sale de dividir la
cantidad incluida por mes por la cantidad de visitas del mes (según
insa_frecuencia_entrega) — ver _pedido_habitual_efectivo en res.partner."""
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

VISITAS_POR_MES = {
    "semanal": 4,
    "quincenal": 2,
    "mensual": 1,
    "a_demanda": 1,
}


class InsaAbonoTipo(models.Model):
    _name = "insa.abono.tipo"
    _description = "Tipo de abono (catálogo de planes)"
    _order = "categoria, name"

    name = fields.Char(string="Nombre", required=True)
    categoria = fields.Selection(
        selection=[("aguas", "Abono Aguas"), ("soda", "Abono Soda")],
        required=True,
    )
    precio = fields.Float(string="Precio del plan")
    linea_ids = fields.One2many(
        comodel_name="insa.abono.tipo.linea",
        inverse_name="abono_tipo_id",
        string="Productos incluidos",
    )
    active = fields.Boolean(default=True)


class InsaAbonoTipoLinea(models.Model):
    _name = "insa.abono.tipo.linea"
    _description = "Producto incluido en un tipo de abono"

    abono_tipo_id = fields.Many2one(
        comodel_name="insa.abono.tipo", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(
        comodel_name="product.template",
        string="Producto",
        required=True,
        domain="[('sale_ok', '=', True), ('es_envase', '=', False)]",
    )
    cantidad_incluida = fields.Float(
        string="Cantidad incluida/mes", required=True, default=1.0
    )
    precio_excedente = fields.Float(string="Precio excedente (unidad)")


class InsaAbono(models.Model):
    _name = "insa.abono"
    _description = "Abono asignado a un cliente"
    _order = "partner_id, active desc, vigencia_desde desc"

    partner_id = fields.Many2one(
        comodel_name="res.partner", required=True, ondelete="cascade", index=True
    )
    abono_tipo_id = fields.Many2one(
        comodel_name="insa.abono.tipo", string="Plan de origen"
    )
    name = fields.Char(string="Nombre", required=True)
    precio = fields.Float(string="Precio")
    vigencia_desde = fields.Date(default=fields.Date.context_today, required=True)
    vigencia_hasta = fields.Date(string="Vigencia hasta", readonly=True, copy=False)
    active = fields.Boolean(default=True, copy=False)
    linea_ids = fields.One2many(
        comodel_name="insa.abono.linea", inverse_name="abono_id", string="Productos"
    )

    @api.onchange("abono_tipo_id")
    def _onchange_abono_tipo_id(self):
        # Copia el tipo como punto de partida — igual que el sistema
        # viejo, donde "Abono hasta 20 bidones" (el tipo) podía terminar
        # con cantidad 10 en la línea real del cliente.
        if self.abono_tipo_id:
            self.name = self.abono_tipo_id.name
            self.precio = self.abono_tipo_id.precio
            self.linea_ids = [Command.clear()] + [
                Command.create(
                    {
                        "product_id": linea.product_id.id,
                        "cantidad_incluida": linea.cantidad_incluida,
                        "precio_excedente": linea.precio_excedente,
                    }
                )
                for linea in self.abono_tipo_id.linea_ids
            ]

    def action_dar_de_baja(self):
        """Reemplazar/dar de baja: queda en el historial (active=False,
        vigencia_hasta hoy), nunca se borra — mismo criterio que "Abonos
        inactivos" del sistema viejo."""
        for rec in self:
            if not rec.active:
                raise UserError(_("Este abono ya está dado de baja."))
            rec.write(
                {"active": False, "vigencia_hasta": fields.Date.context_today(rec)}
            )

    def action_abrir_nuevo_para_cliente(self):
        """Da de baja este abono y abre el formulario de uno nuevo para el
        mismo cliente — el flujo típico de "reemplazar el plan"."""
        self.ensure_one()
        self.action_dar_de_baja()
        return {
            "type": "ir.actions.act_window",
            "name": _("Nuevo abono"),
            "res_model": "insa.abono",
            "view_mode": "form",
            "target": "current",
            "context": {"default_partner_id": self.partner_id.id},
        }


class InsaAbonoLinea(models.Model):
    _name = "insa.abono.linea"
    _description = "Producto incluido en el abono de un cliente"

    abono_id = fields.Many2one(
        comodel_name="insa.abono", required=True, ondelete="cascade"
    )
    active = fields.Boolean(related="abono_id.active", store=True)
    partner_id = fields.Many2one(related="abono_id.partner_id", store=True)
    product_id = fields.Many2one(
        comodel_name="product.template",
        string="Producto",
        required=True,
        domain="[('sale_ok', '=', True), ('es_envase', '=', False)]",
    )
    cantidad_incluida = fields.Float(
        string="Cantidad incluida/mes", required=True, default=1.0
    )
    precio_excedente = fields.Float(string="Precio excedente (unidad)")

    cantidad_por_visita = fields.Float(
        string="Cantidad por visita", compute="_compute_cantidad_por_visita"
    )
    consumido_mes = fields.Float(
        string="Consumido este mes", compute="_compute_consumido_mes"
    )
    excedente_unidades = fields.Float(
        string="Excedente (unidades)", compute="_compute_consumido_mes"
    )

    @api.depends("cantidad_incluida", "partner_id.insa_frecuencia_entrega")
    def _compute_cantidad_por_visita(self):
        for rec in self:
            frecuencia = rec.partner_id.insa_frecuencia_entrega or "semanal"
            visitas = VISITAS_POR_MES.get(frecuencia, 4)
            rec.cantidad_por_visita = rec.cantidad_incluida / visitas if visitas else 0.0

    @api.depends("product_id", "partner_id")
    def _compute_consumido_mes(self):
        # Ciclo calendario: desde el 1° del mes actual, no desde la fecha
        # de alta del abono.
        primer_dia = fields.Date.context_today(self).replace(day=1)
        Linea = self.env["insa.movimiento.cliente.linea.articulo"]
        for rec in self:
            if not rec.product_id or not rec.partner_id:
                rec.consumido_mes = 0.0
                rec.excedente_unidades = 0.0
                continue
            lineas = Linea.search(
                [
                    ("movimiento_id.partner_id", "=", rec.partner_id.id),
                    ("product_id", "=", rec.product_id.id),
                    ("tipo", "=", "venta"),
                    ("movimiento_id.fecha_hora", ">=", primer_dia),
                    ("movimiento_id.hoja_ruta_id.state", "=", "cerrada"),
                ]
            )
            total = sum(lineas.mapped("cantidad"))
            rec.consumido_mes = total
            rec.excedente_unidades = max(total - rec.cantidad_incluida, 0.0)

    def action_vender_excedente(self):
        """Acción manual (como "Vender excedentes al abono" del sistema
        viejo): genera un pedido de venta por lo consumido de más este mes
        calendario, al precio de excedente — aparte del abono en sí, que
        se sigue facturando en el sistema viejo durante el piloto."""
        self.ensure_one()
        if self.excedente_unidades <= 0:
            raise UserError(
                _("%s no tiene excedente para vender este mes.") % self.product_id.name
            )
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner_id.id,
                "origin": _("Excedente de abono — %s") % self.abono_id.name,
                "order_line": [
                    Command.create(
                        {
                            "product_id": self.product_id.product_variant_id.id,
                            "product_uom_qty": self.excedente_unidades,
                            "price_unit": self.precio_excedente,
                        }
                    )
                ],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Venta de excedente"),
            "res_model": "sale.order",
            "res_id": order.id,
            "view_mode": "form",
            "target": "current",
        }
