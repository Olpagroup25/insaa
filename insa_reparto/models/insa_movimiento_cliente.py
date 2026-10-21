# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class _AuditaCorreccionLinea(models.AbstractModel):
    """R-06: las líneas de artículo y de envase de una visita siguen las
    mismas reglas que la visita misma — editables en 'En reparto',
    solo por la administrativa de cierre en 'Consolidando' (con
    auditoría y liquidación recalculada, "el renglón amarillo
    desaparece"), y nunca con la hoja cerrada."""

    _name = "insa.movimiento.cliente.linea.auditada"
    _description = "Línea de visita con control de edición por estado de la hoja"

    def _describir_linea(self):
        self.ensure_one()
        tipo = dict(self._fields["tipo"].selection).get(self.tipo, self.tipo)
        return "%s %g × %s (%s)" % (
            tipo,
            self.cantidad,
            self.product_id.name,
            self.movimiento_id.partner_id.name,
        )

    @api.model_create_multi
    def create(self, vals_list):
        self.env["insa.movimiento.cliente"].browse(
            {vals["movimiento_id"] for vals in vals_list if vals.get("movimiento_id")}
        ).hoja_ruta_id._check_visitas_editables()
        records = super().create(vals_list)
        for rec in records:
            rec.movimiento_id.hoja_ruta_id._registrar_correccion_web(
                _("se agregó %s.") % rec._describir_linea()
            )
        return records

    def write(self, vals):
        self.movimiento_id.hoja_ruta_id._check_visitas_editables()
        antes = {rec.id: rec._describir_linea() for rec in self}
        res = super().write(vals)
        for rec in self:
            rec.movimiento_id.hoja_ruta_id._registrar_correccion_web(
                "%s → %s" % (antes[rec.id], rec._describir_linea())
            )
        return res

    def unlink(self):
        self.movimiento_id.hoja_ruta_id._check_visitas_editables()
        quitadas = [(rec.movimiento_id.hoja_ruta_id, rec._describir_linea()) for rec in self]
        res = super().unlink()
        for hoja, descripcion in quitadas:
            hoja._registrar_correccion_web(_("se quitó %s.") % descripcion)
        return res


class InsaMovimientoCliente(models.Model):
    _name = "insa.movimiento.cliente"
    _description = "Movimiento por cliente (evento de visita)"
    _order = "secuencia"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute",
        string="Hoja de ruta",
        required=True,
        ondelete="cascade",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner", string="Cliente", required=True
    )
    secuencia = fields.Integer(
        string="Secuencia de visita",
        default=10,
        help="C-03: las visitas extra van con secuencia 0 (se agrupan "
        "primero, como en el sistema viejo — 'todo eso tiene orden cero, "
        "cero, cero; el uno para abajo es la hoja de ruta del día'). Se "
        "fuerza a 0 automáticamente cuando es_extra está tildado.",
    )
    fecha_hora = fields.Datetime(default=fields.Datetime.now)

    # -- C-03: pedidos y ventas extra en el mismo listado -----------------
    # Antes vivían en una pestaña aparte (insa.pedido.extra, el "encargo"
    # todavía sin atender). Una vez que el repartidor efectivamente visita
    # a ese cliente, la visita real queda acá, marcada como extra, en el
    # mismo listado que cualquier otra — insa.pedido.extra sigue existiendo
    # solo como la cola de "pedidos pendientes de atender".
    es_extra = fields.Boolean(
        string="Extra",
        default=False,
        help="True cuando esta visita no proviene de la ruta fija del día "
        "(cliente de otro día, alta rápida, o venta puntual encontrada por "
        "el buscador). Equivalente a la 'crucecita roja' del sistema "
        "viejo.",
    )
    origen_extra = fields.Selection(
        selection=[("repartidor", "Repartidor"), ("administracion", "Administración")],
        string="Origen del extra",
        help="Quién agregó esta visita fuera de la ruta fija.",
    )
    pedido_extra_id = fields.Many2one(
        comodel_name="insa.pedido.extra",
        string="Pedido extra de origen",
        help="Si esta visita resuelve un encargo cargado antes por "
        "administración (RN-17), queda vinculado acá a modo de "
        "trazabilidad. Opcional: una venta puntual que el repartidor "
        "encuentra con el buscador no tiene pedido de origen.",
    )

    @api.onchange("es_extra")
    def _onchange_es_extra(self):
        if self.es_extra:
            self.secuencia = 0

    # RN-19: íconos por visita acumulables — visitado/resultado son
    # independientes entre sí (se puede estar "visitado" y a la vez
    # "ausente"), y tiene_venta/tiene_mov_envase/tiene_cobro se derivan
    # solos de lo que efectivamente se cargó.
    visitado = fields.Boolean(
        string="Visitado",
        default=True,
        help="Hubo una visita física al domicilio. Desmarcar para el caso "
        "de 'sin gestión' resuelto por WhatsApp sin ir (RN-18), y para un "
        "cobro administrativo sin venta (C-04) — en ese caso no hubo "
        "visita física, solo se asienta el cobro.",
    )
    resultado = fields.Selection(
        selection=[
            ("ausente", "Ausente"),
            ("sin_gestion", "Sin gestiones"),
            ("repaso", "Repaso"),
        ],
        string="Resultado sin transacción",
        help="Dejar vacío si hubo venta, cobro o movimiento de envase. "
        "'Repaso' (C-26): fui y no estaba, pero me dijo que volviera más "
        "tarde — el cliente sigue pendiente hasta que se resuelve el "
        "mismo día. Comportamiento exacto a confirmar con un repartidor "
        "en la visita a planta (V-12).",
    )
    cobro_efectivo = fields.Float(string="Cobro en efectivo")
    medio_pago = fields.Selection(
        selection=[
            ("efectivo", "Efectivo"),
            ("cheque", "Cheque"),
            ("transferencia", "Transferencia"),
        ],
        string="Medio de pago",
        default="efectivo",
        help="C-04 / RN-10: solo el efectivo entra en el cuadro de "
        "liquidación de efectivo de la hoja de ruta. Un cobro con cheque "
        "o transferencia (por ejemplo, cargado por administración sobre "
        "un cliente que no es del día) queda vinculado acá a modo de "
        "trazabilidad; el asiento contable se resuelve por Contabilidad.",
    )
    linea_articulo_ids = fields.One2many(
        comodel_name="insa.movimiento.cliente.linea.articulo",
        inverse_name="movimiento_id",
        string="Líneas de artículos",
    )
    linea_envase_ids = fields.One2many(
        comodel_name="insa.movimiento.cliente.linea.envase",
        inverse_name="movimiento_id",
        string="Líneas de envases",
    )
    envase_movido = fields.Boolean(default=False, readonly=True, copy=False)
    sale_order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Pedido de venta",
        readonly=True,
        copy=False,
        help="R-02: se genera y confirma al finalizar el reparto, sin "
        "facturar. Mientras la hoja está abierta, la venta vive solo acá.",
    )

    # Campos que escribe el propio cierre (_finalizar): no son una
    # corrección de la visita, no pasan por el control de edición.
    _CAMPOS_SISTEMA = {"sale_order_id", "envase_movido"}

    tiene_venta = fields.Boolean(compute="_compute_iconos", store=True)
    tiene_devolucion_articulo = fields.Boolean(compute="_compute_iconos", store=True)
    tiene_mov_envase = fields.Boolean(compute="_compute_iconos", store=True)
    tiene_cobro = fields.Boolean(compute="_compute_iconos", store=True)

    @api.depends("linea_articulo_ids.tipo", "linea_envase_ids", "cobro_efectivo")
    def _compute_iconos(self):
        for rec in self:
            rec.tiene_venta = bool(
                rec.linea_articulo_ids.filtered(lambda l: l.tipo == "venta")
            )
            rec.tiene_devolucion_articulo = bool(
                rec.linea_articulo_ids.filtered(lambda l: l.tipo == "devolucion")
            )
            rec.tiene_mov_envase = bool(rec.linea_envase_ids)
            rec.tiene_cobro = bool(rec.cobro_efectivo)

    # -- C-13/C-14: pantallazo de préstamos y devoluciones -----------------
    # "Yo quiero saber cuánto se llevaron, cuánto me trajeron, qué me
    # prestaron y qué me devolvieron, en una misma información." No están
    # stored porque deuda_resultante_envase depende de insa.saldo.envase,
    # que a su vez lee stock.quant — Odoo no puede invalidar un campo
    # 'store' a través de esa cadena, así que se recalculan al mostrar la
    # lista (volumen chico: una hoja de ruta tiene, como mucho, un puñado
    # de movimientos con envase por día).
    envases_texto = fields.Char(string="Envase", compute="_compute_envase_resumen")
    prestado_envase = fields.Float(string="Prestados", compute="_compute_envase_resumen")
    devuelto_envase = fields.Float(string="Devueltos", compute="_compute_envase_resumen")
    deuda_previa_envase = fields.Float(
        string="Deuda previa",
        compute="_compute_envase_resumen",
        help="Deuda de envases del cliente antes de esta visita, estimada "
        "revirtiendo el movimiento de hoy sobre la deuda actual "
        "(RN-07: mismo criterio que 'entrada teórica' — previa = "
        "resultante − prestado + devuelto).",
    )
    deuda_resultante_envase = fields.Float(
        string="Deuda resultante", compute="_compute_envase_resumen"
    )

    @api.depends("linea_envase_ids.tipo", "linea_envase_ids.cantidad", "linea_envase_ids.product_id")
    def _compute_envase_resumen(self):
        Saldo = self.env["insa.saldo.envase"]
        for rec in self:
            prestamos = rec.linea_envase_ids.filtered(lambda l: l.tipo == "prestamo")
            devoluciones = rec.linea_envase_ids.filtered(lambda l: l.tipo == "devolucion")
            rec.prestado_envase = sum(prestamos.mapped("cantidad"))
            rec.devuelto_envase = sum(devoluciones.mapped("cantidad"))
            productos = rec.linea_envase_ids.mapped("product_id")
            rec.envases_texto = ", ".join(productos.mapped("name"))
            saldos = (
                Saldo.search(
                    [
                        ("partner_id", "=", rec.partner_id.id),
                        ("product_id", "in", productos.ids),
                    ]
                )
                if productos
                else Saldo.browse()
            )
            rec.deuda_resultante_envase = sum(saldos.mapped("cantidad_a_recuperar"))
            rec.deuda_previa_envase = (
                rec.deuda_resultante_envase - rec.prestado_envase + rec.devuelto_envase
            )

    iconos_visita = fields.Char(
        string="Íconos",
        compute="_compute_iconos_visita",
        store=True,
        help="RN-19 / C-12: íconos acumulables, con las definiciones "
        "exactas que dio el cliente el 08/06 —\n"
        "✔️ Visitado: se le cargó alguna gestión.\n"
        "🛒 Venta: hubo venta de artículos.\n"
        "↩️ Devolución de artículo: producto no conforme, cambio directo "
        "(C-06). En la app va el ícono del sistema viejo, el cuadradito "
        "con flechita.\n"
        "➡️ Movimiento de envases: préstamo o devolución.\n"
        "☕ Ausente: 'fui a tu domicilio y no estabas'.\n"
        "❗ Sin gestiones: 'fui a tu domicilio, vos estás, pero no querés "
        "comprar'. Mantener el naranja, es el que reconocen los "
        "repartidores.\n"
        "🔁 Repaso (C-26): fui y no estaba, pero me dijo que volviera.\n"
        "💲 Cobró: hubo cobro.\n"
        "Se usan los emoji reales en vez de un widget a medida para no "
        "depender de JS custom del lado Odoo; la app sí replica la "
        "iconografía original.",
    )

    @api.depends(
        "visitado",
        "resultado",
        "tiene_venta",
        "tiene_devolucion_articulo",
        "tiene_mov_envase",
        "tiene_cobro",
    )
    def _compute_iconos_visita(self):
        for rec in self:
            partes = []
            if rec.visitado:
                partes.append("✔️")
            if rec.tiene_venta:
                partes.append("🛒")
            if rec.tiene_devolucion_articulo:
                partes.append("↩️")
            if rec.tiene_mov_envase:
                partes.append("➡️")
            if rec.resultado == "ausente":
                partes.append("☕")
            if rec.resultado == "sin_gestion":
                partes.append("❗")
            if rec.resultado == "repaso":
                partes.append("🔁")
            if rec.tiene_cobro:
                partes.append("💲")
            rec.iconos_visita = " ".join(partes)

    @api.model_create_multi
    def create(self, vals_list):
        # C-03: la API y las creaciones directas (a diferencia del form,
        # que dispara el onchange) no pasan por _onchange_es_extra — se
        # fuerza acá para que toda visita extra quede en secuencia 0
        # aunque se cree por XML-RPC/API sin tocar la UI.
        for vals in vals_list:
            if vals.get("es_extra") and "secuencia" not in vals:
                vals["secuencia"] = 0
        self.env["fsm.route.dayroute"].browse(
            {vals["hoja_ruta_id"] for vals in vals_list if vals.get("hoja_ruta_id")}
        )._check_visitas_editables()
        records = super().create(vals_list)
        for rec in records:
            rec.hoja_ruta_id._registrar_correccion_web(
                _("se agregó la visita de %s.") % rec.partner_id.name
            )
        return records

    def write(self, vals):
        if set(vals) <= self._CAMPOS_SISTEMA:
            return super().write(vals)
        hojas = self.hoja_ruta_id
        if vals.get("hoja_ruta_id"):
            hojas |= self.env["fsm.route.dayroute"].browse(vals["hoja_ruta_id"])
        hojas._check_visitas_editables()
        antes = {rec.id: rec._describir_visita() for rec in self}
        res = super().write(vals)
        for rec in self:
            rec.hoja_ruta_id._registrar_correccion_web(
                "%s → %s" % (antes[rec.id], rec._describir_visita())
            )
        return res

    def unlink(self):
        self.hoja_ruta_id._check_visitas_editables()
        eliminadas = [(rec.hoja_ruta_id, rec.partner_id.name) for rec in self]
        res = super().unlink()
        for hoja, cliente in eliminadas:
            hoja._registrar_correccion_web(_("se eliminó la visita de %s.") % cliente)
        return res

    def _describir_visita(self):
        self.ensure_one()
        resultado = dict(self._fields["resultado"].selection).get(self.resultado)
        partes = [self.partner_id.name]
        if resultado:
            partes.append(resultado)
        if self.cobro_efectivo:
            partes.append(_("cobro $ %(monto)s (%(medio)s)") % {
                "monto": "{:,.2f}".format(self.cobro_efectivo),
                "medio": self.medio_pago,
            })
        return ", ".join(partes)

    @api.constrains("hoja_ruta_id")
    def _check_hoja_ruta_iniciada(self):
        # RN-01 / R-09: no puede haber visitas antes del control de playa
        # de salida aprobado ("bloqueo real", validado por Santi el 14/09).
        # Hasta esa reunión lo garantizaba el estado Borrador de la hoja,
        # que ya no existe (R-01).
        for rec in self:
            if not rec.hoja_ruta_id.control_salida_aprobado:
                raise UserError(
                    _(
                        "No se pueden registrar visitas: el control de playa "
                        "de salida de %s todavía no fue aprobado por un "
                        "controlador."
                    )
                    % rec.hoja_ruta_id.display_name
                )

    def _crear_pedido_venta(self):
        """R-02 (decisión del 24/09): al finalizar el reparto, las ventas
        de cada visita pasan a un pedido de venta confirmado del cliente,
        sin facturar — queda "a facturar" para la facturación mensual a
        mes vencido (F-04). Durante el piloto la facturación sigue en el
        sistema viejo (A-2): nadie factura estos pedidos en Odoo todavía.

        Una visita, un pedido. Idempotente: si ya lo tiene, no lo repite.
        El precio es el que cargó el repartidor en la visita (se respeta
        tal cual, no se recalcula con la lista de precios)."""
        for mov in self:
            if mov.sale_order_id:
                continue
            ventas = mov.linea_articulo_ids.filtered(
                lambda l: l.tipo == "venta" and l.cantidad > 0
            )
            if not ventas:
                continue
            order = self.env["sale.order"].create(
                {
                    "partner_id": mov.partner_id.id,
                    "date_order": mov.fecha_hora or fields.Datetime.now(),
                    "origin": mov.hoja_ruta_id.name,
                    "order_line": [
                        Command.create(
                            {
                                "product_id": linea.product_id.product_variant_id.id,
                                "product_uom_qty": linea.cantidad,
                                "price_unit": linea.precio_unitario,
                            }
                        )
                        for linea in ventas
                    ],
                }
            )
            # skip_procurement: la mercadería ya la entregó el repartidor, y
            # el stock de llenos lo controla la playa, no un remito. Si
            # sale_stock está instalado (se autoinstala junto con sale +
            # stock), sin esto cada visita dejaría una entrega pendiente en
            # Inventario.
            order.with_context(skip_procurement=True).action_confirm()
            # Entregado en el acto: sin esto el pedido nunca quedaría "a
            # facturar" para productos que se facturan por lo entregado.
            for line in order.order_line:
                line.qty_delivered = line.product_uom_qty
            mov.sale_order_id = order

    def action_confirmar(self):
        """Genera los stock.move de envase por cada línea de envase, del
        camión (ubicación de stock del almacén de la ruta) al cliente
        (property_stock_customer) o viceversa según prestamo/devolución.

        Simplificación de este spike: se usa la ubicación de stock del
        almacén por defecto como "camión", porque fsm.route no tiene
        almacén propio — a revisar con Lucas si hace falta una
        ubicación por vehículo/ruta en vez de una genérica de almacén.
        """
        for rec in self:
            if rec.envase_movido:
                continue
            camion = self.env["stock.warehouse"].search([], limit=1).lot_stock_id
            cliente_loc = rec.partner_id.property_stock_customer
            if not cliente_loc:
                raise UserError(
                    _("El cliente %s no tiene ubicación de stock asignada.")
                    % rec.partner_id.name
                )
            for linea in rec.linea_envase_ids:
                envase = linea.product_id.envase_id
                if not envase:
                    raise UserError(
                        _("El producto %s no tiene un envase vinculado.")
                        % linea.product_id.name
                    )
                origen, destino = (
                    (camion, cliente_loc)
                    if linea.tipo == "prestamo"
                    else (cliente_loc, camion)
                )
                move = self.env["stock.move"].create(
                    {
                        "name": f"{rec.partner_id.name} - {envase.name}",
                        "product_id": envase.product_variant_id.id,
                        "product_uom_qty": linea.cantidad,
                        "product_uom": envase.uom_id.id,
                        "location_id": origen.id,
                        "location_dest_id": destino.id,
                    }
                )
                move._action_confirm()
                move._action_assign()
                move.quantity = linea.cantidad
                move.picked = True
                move._action_done()
            rec.envase_movido = True
        return True


class InsaMovimientoClienteLineaArticulo(models.Model):
    _name = "insa.movimiento.cliente.linea.articulo"
    _inherit = ["insa.movimiento.cliente.linea.auditada"]
    _description = "Línea de venta o devolución de artículos"

    movimiento_id = fields.Many2one(
        comodel_name="insa.movimiento.cliente", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(
        comodel_name="product.template", string="Producto", required=True
    )
    tipo = fields.Selection(
        selection=[("venta", "Venta"), ("devolucion", "Devolución de artículo")],
        required=True,
        default="venta",
        help="C-06 (confirmado en la reunión del 08/06; antes era el "
        "'Paso 1.8, sin confirmar'): distingue una venta de una devolución "
        "de producto no conforme. La devolución se concilia contra los "
        "fallados del control de playa de entrada (C-07).",
    )
    motivo = fields.Selection(
        selection=[
            ("pinchado", "Pinchado"),
            ("hinchado", "Hinchado"),
            ("calidad", "Falla de calidad"),
            ("otro", "Otro"),
        ],
        string="Motivo de la devolución",
        help="Por qué el producto no está conforme. Textual del cliente: "
        "'hay un producto que no está conforme, porque está pinchado, "
        "porque hay alguna falla de calidad'.",
    )
    cambio_directo = fields.Boolean(
        string="Cambio directo",
        default=True,
        help="C-06: se le entrega uno nuevo en el acto. 'Yo te dejé el "
        "jueves dos bidones, uno estaba pinchado, y esta semana me pedís "
        "que te lo cambie'. No genera venta ni cobro: sale un lleno y "
        "entra un fallado. Desmarcarlo si el cliente devuelve sin "
        "reposición.",
    )
    cantidad = fields.Float(string="Cantidad", default=1.0)
    precio_unitario = fields.Float(string="Precio unitario")
    subtotal = fields.Float(string="Subtotal", compute="_compute_subtotal", store=True)

    @api.depends("cantidad", "precio_unitario", "tipo")
    def _compute_subtotal(self):
        for rec in self:
            # C-06: el cambio directo no genera venta ni cobro. Una
            # devolución nunca factura, aunque quede cargado un precio de
            # referencia en la línea.
            if rec.tipo == "devolucion":
                rec.subtotal = 0.0
            else:
                rec.subtotal = rec.cantidad * rec.precio_unitario

    @api.constrains("tipo", "motivo")
    def _check_motivo_devolucion(self):
        # RN-24: toda devolución de no conforme queda registrada en la
        # visita del cliente con su motivo — es lo que después se cruza
        # con el control de playa.
        for rec in self:
            if rec.tipo == "devolucion" and not rec.motivo:
                raise ValidationError(
                    _(
                        "%s: una devolución de artículo necesita el motivo "
                        "(pinchado, hinchado, falla de calidad u otro)."
                    )
                    % rec.product_id.name
                )


class InsaMovimientoClienteLineaEnvase(models.Model):
    _name = "insa.movimiento.cliente.linea.envase"
    _inherit = ["insa.movimiento.cliente.linea.auditada"]
    _description = "Línea de envases (préstamo/devolución)"

    movimiento_id = fields.Many2one(
        comodel_name="insa.movimiento.cliente", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(
        comodel_name="product.template",
        string="Producto retornable",
        required=True,
        domain=[("es_retornable", "=", True)],
    )
    tipo = fields.Selection(
        selection=[("prestamo", "Préstamo"), ("devolucion", "Devolución")],
        required=True,
    )
    cantidad = fields.Float(string="Cantidad", default=1.0)
    sin_alerta_previa = fields.Boolean(
        string="Sin alerta previa",
        readonly=True,
        copy=False,
        help="Paso 1.10: se completa solo al crear la línea. True si, en "
        "ese momento, el cliente no tenía deuda de este envase (RN 3.4 "
        "del doc D1: la app le muestra al repartidor la deuda ANTES de "
        "la visita, para que la reclame) — distingue una devolución "
        "esperada de una que el sistema no había anticipado. Solo tiene "
        "sentido para tipo='devolucion'; en préstamo queda siempre False.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        Saldo = self.env["insa.saldo.envase"].sudo()
        for rec in records:
            if rec.tipo != "devolucion":
                continue
            saldo = Saldo.search(
                [
                    ("partner_id", "=", rec.movimiento_id.partner_id.id),
                    ("product_id", "=", rec.product_id.id),
                ],
                limit=1,
            )
            rec.sin_alerta_previa = not saldo or saldo.cantidad_a_recuperar <= 0
        return records
