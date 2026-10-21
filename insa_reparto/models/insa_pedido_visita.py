# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""Revisión 03/10: fusión de Pedidos (fsm.order, parada programada) y
Visitas (insa.movimiento.cliente, lo que efectivamente pasó) en una sola
fila por cliente. Pedido reformulado del cliente: "una pestaña es control
de playa de salida, otra muy diferente, la fusión de visitas con
pedidos... pedidos es un estado donde vemos si el cliente realizó el
pedido o no, y las visitas también están asociadas al cliente y a lo que
pidió y al estado en donde se encuentra".

Vista SQL porque ninguno de los dos modelos alcanza solo: un cliente
programado puede no tener visita todavía (el camión no pasó), y una
visita Extra (C-03) puede no tener pedido programado — un FULL OUTER JOIN
por hoja_ruta_id+cliente cubre los tres casos (solo pedido, solo visita,
ambos) en una sola fila."""
from odoo import _, api, fields, models, tools


class InsaPedidoVisita(models.Model):
    _name = "insa.pedido.visita"
    _description = "Pedido y visita por cliente (vista unificada)"
    _auto = False
    _order = "sequence, id"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute", string="Hoja de ruta", readonly=True
    )
    partner_id = fields.Many2one(comodel_name="res.partner", string="Cliente", readonly=True)
    fsm_order_id = fields.Many2one(comodel_name="fsm.order", string="Pedido", readonly=True)
    movimiento_cliente_id = fields.Many2one(
        comodel_name="insa.movimiento.cliente", string="Visita", readonly=True
    )
    sequence = fields.Integer(string="Secuencia", readonly=True)

    tiene_pedido = fields.Boolean(string="Programado", compute="_compute_tiene_pedido")
    tiene_visita = fields.Boolean(string="Con visita", compute="_compute_tiene_visita")
    pedido_stage_id = fields.Many2one(
        related="fsm_order_id.stage_id", string="Estado del pedido"
    )
    es_extra = fields.Boolean(related="movimiento_cliente_id.es_extra", string="Extra")
    origen_extra = fields.Selection(
        related="movimiento_cliente_id.origen_extra", string="Origen del extra"
    )
    iconos_visita = fields.Char(related="movimiento_cliente_id.iconos_visita", string="Íconos")
    visitado = fields.Boolean(related="movimiento_cliente_id.visitado", string="Visitado")
    resultado = fields.Selection(
        related="movimiento_cliente_id.resultado", string="Resultado sin transacción"
    )
    medio_pago = fields.Selection(
        related="movimiento_cliente_id.medio_pago", string="Medio de pago"
    )
    cobro_efectivo = fields.Float(
        related="movimiento_cliente_id.cobro_efectivo", string="Cobro en efectivo"
    )

    @api.depends("fsm_order_id")
    def _compute_tiene_pedido(self):
        for rec in self:
            rec.tiene_pedido = bool(rec.fsm_order_id)

    @api.depends("movimiento_cliente_id")
    def _compute_tiene_visita(self):
        for rec in self:
            rec.tiene_visita = bool(rec.movimiento_cliente_id)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            """
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    CASE WHEN o.id IS NOT NULL THEN o.id ELSE -m.id END AS id,
                    COALESCE(o.dayroute_id, m.hoja_ruta_id) AS hoja_ruta_id,
                    COALESCE(loc.partner_id, m.partner_id) AS partner_id,
                    o.id AS fsm_order_id,
                    m.id AS movimiento_cliente_id,
                    COALESCE(o.sequence, m.secuencia, 10) AS sequence
                FROM fsm_order o
                LEFT JOIN fsm_location loc ON loc.id = o.location_id
                FULL JOIN insa_movimiento_cliente m
                    ON m.hoja_ruta_id = o.dayroute_id
                   AND m.partner_id = loc.partner_id
            )
            """
            % self._table
        )

    def action_abrir_visita(self):
        """Abre la visita existente, o la crea en blanco si este cliente
        todavía solo tiene el pedido programado (el camión no pasó)."""
        self.ensure_one()
        movimiento = self.movimiento_cliente_id
        if not movimiento:
            self.hoja_ruta_id._check_visitas_editables()
            movimiento = self.env["insa.movimiento.cliente"].create(
                {
                    "hoja_ruta_id": self.hoja_ruta_id.id,
                    "partner_id": self.partner_id.id,
                    "secuencia": self.sequence or 10,
                }
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Visita"),
            "res_model": "insa.movimiento.cliente",
            "res_id": movimiento.id,
            "view_mode": "form",
            "target": "new",
        }
