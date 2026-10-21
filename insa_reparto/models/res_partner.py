# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import _, api, fields, models

# El formulario público de alta pide la condición fiscal en 4 opciones
# simples (insa_tax_condition), pero lo que usa la facturación electrónica
# real es el campo argentino nativo (l10n_ar_afip_responsibility_type_id,
# más opciones — Monotributo Social, Sujeto no Categorizado, etc.). Se
# mapea por "code" del catálogo AFIP en vez de por id de base de datos,
# para que no dependa de qué ids terminó teniendo cada instalación.
INSA_TAX_CONDITION_AFIP_CODE = {
    "consumidor_final": "5",
    "responsable_inscripto": "1",
    "monotributista": "6",
    "exento": "4",
}


class ResPartner(models.Model):
    _inherit = "res.partner"

    alias_bancario = fields.Char(
        string="Alias bancario",
        help="Formato INSA.<nro_cliente>. Insumo para la conciliación "
        "bancaria automática (Fase 2), no se usa todavía en D1.",
    )
    insa_saldo_envase_ids = fields.One2many(
        comodel_name="insa.saldo.envase",
        inverse_name="partner_id",
        string="Saldo de envases",
    )
    insa_ubicacion_stock_creada = fields.Boolean(
        default=False,
        copy=False,
        help="Marca interna: evita crear dos veces la ubicación de stock "
        "dedicada del cliente. No usar 'if property_stock_customer' para "
        "esto — es un campo company_dependent y siempre devuelve un valor "
        "truthy (el default genérico) aunque no se haya seteado nada.",
    )
    insa_picking_ids = fields.One2many(
        comodel_name="stock.picking", inverse_name="partner_id", string="Entregas (INSA)"
    )
    insa_payment_ids = fields.One2many(
        comodel_name="account.payment", inverse_name="partner_id", string="Cobros (INSA)"
    )

    # -- Formulario de alta público (feature-formulario-alta) ---------------
    # Domicilio estructurado (formato argentino) + datos de facturación,
    # capturados por /formulario-alta-insa (controllers/
    # insa_registration_controller.py). No toca customer_rank a propósito:
    # un alta acá es un lead/contacto nuevo, no un cliente de reparto
    # confirmado — no aparece en el Buscador (Paso 2.6) ni puede recibir
    # visitas hasta que INSA lo convierta en cliente por el circuito
    # normal de Odoo.
    insa_city_id = fields.Many2one(comodel_name="insa.city", string="Ciudad o Barrio")
    insa_street_id = fields.Many2one(comodel_name="insa.street", string="Calle")
    insa_street_number = fields.Char(string="Número")
    insa_floor = fields.Char(string="Piso")
    insa_apartment = fields.Char(string="Departamento")
    insa_tower = fields.Char(string="Torre")
    insa_block = fields.Char(string="Manzana")
    insa_lot = fields.Char(string="Lote")
    insa_address_notes = fields.Text(string="Referencias del domicilio")
    insa_tax_condition = fields.Selection(
        selection=[
            ("consumidor_final", "Consumidor Final"),
            ("responsable_inscripto", "Responsable Inscripto"),
            ("monotributista", "Monotributista"),
            ("exento", "Exento"),
        ],
        string="Condición fiscal",
        default="consumidor_final",
    )
    insa_order_notes = fields.Text(
        string="Pedidos u observaciones (alta)",
        help="Dato heredado: texto libre que llega del alta web o de la "
        "app al crear el cliente. Se mantiene de referencia — 4.3 lo "
        "reemplaza como pedido de verdad por 'Pedido habitual', con "
        "productos y cantidades.",
    )
    insa_frecuencia_entrega = fields.Selection(
        selection=[
            ("semanal", "Semanal"),
            ("quincenal", "Quincenal"),
            ("mensual", "Mensual"),
            ("a_demanda", "A demanda"),
        ],
        string="Frecuencia de entrega",
        default="semanal",
    )
    insa_dias_entrega = fields.Char(
        string="Días de entrega",
        help="Días de la semana en los que se entrega (ej: 'Lu, Ju')",
    )
    # 4.3 (revisión 29/09): pedido habitual con productos reales, editable
    # desde la pestaña INSA - Reparto. Separado de consumo_promedio (que
    # solo aplica a envases retornables, para la tolerancia de saldo).
    pedido_habitual_ids = fields.One2many(
        comodel_name="insa.pedido.habitual.linea",
        inverse_name="partner_id",
        string="Pedido habitual",
    )
    # Revisión 07/10: abonos (migrado de Water Service, pantalla "Clientes
    # → Abonos"). abono_ids trae solo los activos (active=True es el
    # default de un Many2one estándar hacia un modelo con _active ya
    # filtrado); abono_todos_ids incluye también los dados de baja, para
    # mostrar el historial completo en la ficha.
    abono_ids = fields.One2many(
        comodel_name="insa.abono", inverse_name="partner_id", string="Abonos"
    )
    abono_todos_ids = fields.One2many(
        comodel_name="insa.abono",
        inverse_name="partner_id",
        string="Abonos (con historial)",
        context={"active_test": False},
    )

    def _pedido_habitual_efectivo(self):
        """4.3 + abonos (07/10): cantidad por producto a precargar en el
        control de salida. Un producto con abono activo usa la cantidad
        por visita del abono (cantidad incluida/mes ÷ visitas del mes);
        el resto sigue viniendo del pedido habitual manual. El abono
        "reemplaza" al pedido habitual solo para los productos que cubre,
        no lo invalida entero."""
        self.ensure_one()
        por_producto = {
            linea.product_id: linea.cantidad for linea in self.pedido_habitual_ids
        }
        for abono in self.abono_ids:
            for linea in abono.linea_ids:
                por_producto[linea.product_id] = linea.cantidad_por_visita
        return por_producto
    # R-08 (reunión 14/09): "la hoja de ruta diaria trae solo clientes
    # activos". El estado del cliente llegó con la migración de Water
    # Service como campo manual (x_, creado por XML-RPC, sin módulo
    # dueño); se formaliza acá con el mismo nombre y los mismos seis
    # valores para que el código lo pueda usar sin depender de que exista
    # en la base. Cambiar un valor de la lista borraría el estado de los
    # clientes que lo tienen: no tocar sin migración.
    x_insa_estado_ws = fields.Selection(
        selection=[
            ("activo", "Activo"),
            ("no_consume", "No consume"),
            ("moroso", "Moroso"),
            ("baja", "Baja"),
            ("borrador", "Borrador"),
            ("gestion_baja", "En gestión de baja"),
        ],
        string="Estado (WS)",
        help="Estado del cliente migrado de Water Service. Solo los "
        "clientes en 'Activo' (o sin estado, como un alta nueva hecha en "
        "Odoo) entran en la hoja de ruta diaria.",
    )
    estado_completitud = fields.Selection(
        selection=[("incompleto", "Incompleto"), ("completo", "Completo")],
        string="Estado de los datos",
        default="completo",
        help="C-20: un alta rápida desde la app (nombre, dirección, "
        "teléfono — sin datos de facturación) queda en 'Incompleto' para "
        "que administración lo termine de cargar después.",
    )

    # -- C-22: FUE y FUC ---------------------------------------------------
    # "FUE significa fecha de última entrega: la última vez que yo te
    # entregué un producto. FUC, fecha de último cobro: la última vez que
    # vos me entregaste dinero a mí."
    #
    # Hallazgo H-2 (Sección 3): hasta acá esto se computaba desde
    # insa_picking_ids (stock.picking tipo 'outgoing') — el flujo de
    # reparto NUNCA genera esos pickings, la venta se registra en
    # insa.movimiento.cliente y solo los envases producen stock.move. El
    # campo quedaba vacío para siempre y el cron de FUE nunca disparaba.
    # Se recomputa acá desde el dato real: la propia visita.
    #
    # Simplificación registrada (no es una decisión, es una omisión
    # consciente por alcance): un cambio directo (C-06, tipo='devolucion'
    # con cambio_directo=True) también entrega un producto nuevo en el
    # acto, pero no cuenta como "entrega" para el FUE — solo se mira
    # tiene_venta. Ajustar si en el uso real resulta relevante.
    insa_movimiento_ids = fields.One2many(
        comodel_name="insa.movimiento.cliente",
        inverse_name="partner_id",
        string="Visitas (INSA)",
    )
    fecha_ultima_entrega = fields.Date(
        string="Fecha última entrega (FUE)",
        compute="_compute_fue_fuc",
        store=True,
        help="Última visita con venta de una hoja de ruta cerrada (R-02: "
        "nada impacta en el cliente antes del cierre), o la del sistema "
        "anterior si es más reciente.",
    )
    fecha_ultima_cobranza = fields.Date(
        string="Fecha último cobro (FUC)",
        compute="_compute_fue_fuc",
        store=True,
        help="Último cobro en efectivo de una hoja de ruta cerrada, o "
        "último pago (cheque/transferencia) conciliado por Contabilidad, "
        "o el del sistema anterior si es más reciente.",
    )
    # Piso de FUE/FUC: la migración de Water Service escribió la fecha
    # real directamente en fecha_ultima_entrega/fecha_ultima_cobranza,
    # que son computadas. Sin este piso, el primer recálculo (cualquier
    # visita o pago nuevo del cliente, o el cierre de una hoja donde
    # estuvo ausente) las volvía a calcular solo con lo que hay en Odoo y
    # borraba el histórico migrado. Se completan una única vez en la
    # migración 18.0.1.7.0 con el valor que tenían.
    fue_legado = fields.Date(
        string="FUE del sistema anterior",
        readonly=True,
        help="Última entrega según Water Service, al momento de migrar.",
    )
    fuc_legado = fields.Date(
        string="FUC del sistema anterior",
        readonly=True,
        help="Último cobro según Water Service, al momento de migrar.",
    )
    dias_sin_entrega = fields.Integer(
        string="Días sin entrega (FUE)", compute="_compute_dias_sin"
    )
    dias_sin_cobro = fields.Integer(
        string="Días sin cobro (FUC)", compute="_compute_dias_sin"
    )
    umbral_alerta_entrega = fields.Integer(
        string="Umbral alerta FUE (días)",
        default=30,
        help="RN-29: parametrizable por cliente y editable en masa "
        "seleccionando por etiqueta ('agarro todos mis clientes que "
        "tengan la etiqueta de empresa y quiero que me avise el sistema "
        "cuando pasen 30 días'). Referencia del cliente: consumo 20-30 "
        "días, según verano/invierno (V-15, variación estacional "
        "automática todavía sin definir).",
    )
    umbral_alerta_cobro = fields.Integer(
        string="Umbral alerta FUC (días)",
        default=30,
        help="RN-29. Referencia del cliente: empresa 45 días, casa de "
        "familia 15 a 30 días según zona (V-15).",
    )
    # Sin store=True a propósito: dependen de "hoy" (vía dias_sin_entrega/
    # dias_sin_cobro), no solo de campos guardados. Un campo store=True no
    # se recalcula porque pase el tiempo, solo cuando algo en su cadena de
    # @api.depends se ESCRIBE — con "hoy" en el medio quedaría congelado
    # en lo que valía el día que se guardó.
    fue_vencido = fields.Boolean(string="FUE vencido", compute="_compute_vencidos")
    fuc_vencido = fields.Boolean(string="FUC vencido", compute="_compute_vencidos")

    @api.depends(
        "insa_movimiento_ids.fecha_hora",
        "insa_movimiento_ids.tiene_venta",
        "insa_movimiento_ids.tiene_cobro",
        "insa_movimiento_ids.medio_pago",
        "insa_movimiento_ids.hoja_ruta_id.state",
        "insa_payment_ids.date",
        "insa_payment_ids.state",
        "insa_payment_ids.payment_type",
        "fue_legado",
        "fuc_legado",
    )
    def _compute_fue_fuc(self):
        for partner in self:
            # R-02: "hasta que el reparto no dice cerrado, nada de lo que
            # haya cargado el repartidor efectivamente migra a la cuenta
            # del cliente" — la visita de una hoja todavía abierta no
            # mueve FUE ni FUC.
            cerradas = partner.insa_movimiento_ids.filtered(
                lambda m: m.hoja_ruta_id.state == "cerrada"
            )
            fechas_entrega = [m.fecha_hora.date() for m in cerradas.filtered("tiene_venta")]
            if partner.fue_legado:
                fechas_entrega.append(partner.fue_legado)
            partner.fecha_ultima_entrega = max(fechas_entrega) if fechas_entrega else False
            # RN-10: solo el cobro en efectivo queda en la propia visita;
            # cheque/transferencia se concilian por Contabilidad
            # (account.payment, insa_payment_ids) — FUC mira las dos
            # fuentes.
            fechas_cobro = [
                m.fecha_hora.date()
                for m in cerradas.filtered(
                    lambda m: m.tiene_cobro and m.medio_pago == "efectivo"
                )
            ]
            pagos = partner.insa_payment_ids.filtered(
                lambda p: p.payment_type == "inbound" and p.state == "paid"
            )
            fechas_cobro += [d for d in pagos.mapped("date") if d]
            if partner.fuc_legado:
                fechas_cobro.append(partner.fuc_legado)
            partner.fecha_ultima_cobranza = max(fechas_cobro) if fechas_cobro else False

    @api.depends("fecha_ultima_entrega", "fecha_ultima_cobranza")
    def _compute_dias_sin(self):
        hoy = fields.Date.context_today(self)
        for partner in self:
            partner.dias_sin_entrega = (
                (hoy - partner.fecha_ultima_entrega).days if partner.fecha_ultima_entrega else 0
            )
            partner.dias_sin_cobro = (
                (hoy - partner.fecha_ultima_cobranza).days if partner.fecha_ultima_cobranza else 0
            )

    @api.depends(
        "dias_sin_entrega", "umbral_alerta_entrega", "fecha_ultima_entrega",
        "dias_sin_cobro", "umbral_alerta_cobro", "fecha_ultima_cobranza",
    )
    def _compute_vencidos(self):
        for partner in self:
            partner.fue_vencido = bool(partner.fecha_ultima_entrega) and (
                partner.dias_sin_entrega > partner.umbral_alerta_entrega
            )
            partner.fuc_vencido = bool(partner.fecha_ultima_cobranza) and (
                partner.dias_sin_cobro > partner.umbral_alerta_cobro
            )

    def _crear_oportunidad_recompra(self):
        """C-23: "FUE vencido → crea actividad/oportunidad en CRM y
        dispara mensaje automático: 'che, hace quince días que no me
        comprás, ¿querés repetir tu pedido?'". Una oportunidad abierta
        por cliente a la vez — no una nueva cada día que corre el cron
        mientras sigue vencido."""
        self.ensure_one()
        Lead = self.env["crm.lead"].sudo()
        tag = self.env["crm.tag"].sudo().search([("name", "=", "FUE vencido")], limit=1)
        if not tag:
            tag = self.env["crm.tag"].sudo().create({"name": "FUE vencido"})
        existe = Lead.search_count(
            [
                ("partner_id", "=", self.id),
                ("type", "=", "opportunity"),
                ("active", "=", True),
                ("tag_ids", "in", tag.id),
            ]
        )
        if existe:
            return
        Lead.create(
            {
                "name": _("Recompra — %s") % self.name,
                "partner_id": self.id,
                "type": "opportunity",
                "tag_ids": [(4, tag.id)],
                "description": _(
                    "Hace %(dias)s días que no compra (FUE vencido, umbral "
                    "%(umbral)s días). 'Che, hace %(dias)s días que no me "
                    "comprás, ¿querés repetir tu pedido?'"
                )
                % {"dias": self.dias_sin_entrega, "umbral": self.umbral_alerta_entrega},
            }
        )

    def _cron_check_fue(self):
        """RN-29: umbral parametrizable por cliente (ya no fijo en 30
        días). FUE vencido dispara una actividad interna Y una
        oportunidad de recompra en CRM (C-23); FUC vencido solo la
        alerta de cobranza sobre la ficha — no es un evento comercial.
        El canal real de notificación (push/email/WhatsApp) sigue sin
        confirmar (ver 'Preguntas para el cliente' del plan de trabajo)."""
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        responsable = self.env.ref("insa_reparto.group_supervisor").users[:1]
        candidatos = self.search(
            [
                # Bug encontrado en el Paso 2.6: type == "fsm_location" no
                # matchea nunca un cliente real — customer_rank > 0 es el
                # criterio estándar de Odoo para "es un cliente".
                ("customer_rank", ">", 0),
                "|",
                ("fecha_ultima_entrega", "!=", False),
                ("fecha_ultima_cobranza", "!=", False),
            ]
        )

        for partner in candidatos.filtered("fue_vencido"):
            summary = _("FUE vencido (%s+ días sin entrega)") % partner.umbral_alerta_entrega
            ya_existe = self.env["mail.activity"].search_count(
                [
                    ("res_model", "=", "res.partner"),
                    ("res_id", "=", partner.id),
                    ("activity_type_id", "=", activity_type.id),
                    ("summary", "=", summary),
                ]
            )
            if not ya_existe:
                partner.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=summary,
                    note=_(
                        "Umbral parametrizable por cliente (RN-29). Canal "
                        "de notificación real (push/email/WhatsApp) sin "
                        "confirmar todavía."
                    ),
                    user_id=(responsable.id if responsable else self.env.user.id),
                )
            partner._crear_oportunidad_recompra()

        for partner in candidatos.filtered("fuc_vencido"):
            summary = _("Factura vencida (%s+ días sin cobro)") % partner.umbral_alerta_cobro
            ya_existe = self.env["mail.activity"].search_count(
                [
                    ("res_model", "=", "res.partner"),
                    ("res_id", "=", partner.id),
                    ("activity_type_id", "=", activity_type.id),
                    ("summary", "=", summary),
                ]
            )
            if ya_existe:
                continue
            partner.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=summary,
                note=_("C-23: alerta de cobranza — FUC vencido."),
                user_id=(responsable.id if responsable else self.env.user.id),
            )

    @api.model
    def _insa_afip_id_from_tax_condition(self, tax_condition):
        code = INSA_TAX_CONDITION_AFIP_CODE.get(tax_condition)
        if not code:
            return False
        tipo = self.env["l10n_ar.afip.responsibility.type"].search(
            [("code", "=", code)], limit=1
        )
        return tipo.id or False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("insa_tax_condition") and not vals.get(
                "l10n_ar_afip_responsibility_type_id"
            ):
                afip_id = self._insa_afip_id_from_tax_condition(vals["insa_tax_condition"])
                if afip_id:
                    vals["l10n_ar_afip_responsibility_type_id"] = afip_id
        partners = super().create(vals_list)
        # Bug encontrado en el Paso 2.6 (mismo que en _cron_check_fue de
        # arriba): type == "fsm_location" nunca matchea un cliente real.
        # Sin este fix, la ubicación de stock dedicada por cliente —la
        # base de todo el tracking de saldo de envases, RN 3.4 / Paso
        # 1.10— nunca se creaba para ningún cliente real.
        partners.filtered(lambda p: p.customer_rank > 0)._insa_crear_ubicacion_stock()
        return partners

    def write(self, vals):
        if vals.get("insa_tax_condition") and "l10n_ar_afip_responsibility_type_id" not in vals:
            afip_id = self._insa_afip_id_from_tax_condition(vals["insa_tax_condition"])
            if afip_id:
                vals = dict(vals, l10n_ar_afip_responsibility_type_id=afip_id)
        res = super().write(vals)
        if "customer_rank" in vals:
            self.filtered(lambda p: p.customer_rank > 0)._insa_crear_ubicacion_stock()
        return res

    def _insa_crear_ubicacion_stock(self):
        """Crea una stock.location dedicada por cliente y la asigna como
        property_stock_customer, para poder trazar el saldo de envases
        de cada cliente por separado (en vez de compartir la ubicación
        genérica "Customers")."""
        parent = self.env.ref("stock.stock_location_customers")
        for partner in self:
            if partner.insa_ubicacion_stock_creada:
                continue
            location = self.env["stock.location"].create(
                {
                    "name": partner.name,
                    "usage": "customer",
                    "location_id": parent.id,
                }
            )
            partner.property_stock_customer = location.id
            partner.insa_ubicacion_stock_creada = True

    def _insa_activo_para_reparto(self):
        """R-08 (decisión del 24/09: solo 'Activo'). Moroso, No consume,
        En gestión de baja, Baja y Borrador quedan afuera de la hoja de
        ruta diaria. Sin estado también entra: es un cliente dado de alta
        en Odoo (alta rápida, C-20), que no pasó por Water Service."""
        self.ensure_one()
        return self.active and self.x_insa_estado_ws in (False, "activo")
