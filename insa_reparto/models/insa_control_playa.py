# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, UserError, ValidationError


class InsaControlPlaya(models.Model):
    _name = "insa.control.playa"
    _description = "Control de playa (salida/entrada)"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute",
        string="Hoja de ruta",
        required=True,
        ondelete="cascade",
    )
    tipo = fields.Selection(
        selection=[("salida", "Salida"), ("entrada", "Entrada")],
        required=True,
    )
    fecha_hora = fields.Datetime(default=fields.Datetime.now, required=True)
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Cargado por",
        default=lambda self: self.env.user,
        help="C-08: el repartidor que hizo el conteo. No es quien lo "
        "aprueba — eso es aprobado_por_id, y tiene que ser otra persona "
        "(un controlador de playa).",
    )
    state = fields.Selection(
        selection=[("borrador", "Borrador"), ("confirmado", "Confirmado")],
        string="Estado",
        default="borrador",
        required=True,
        copy=False,
        help="C-08 / RN-22: el repartidor carga el conteo (borrador) y el "
        "controlador lo aprueba con su usuario y contraseña (confirmado). "
        "Ningún control queda confirmado sin esa aprobación.",
    )
    kilometros = fields.Float(
        string="Kilómetros",
        help="Lectura del odómetro al momento del control. No surge del "
        "documento D1 ni de la reunión — viene del diseño de "
        "ControlDePlayaDialog (D3); se agrega igual por ser de bajo "
        "riesgo, a diferencia de Dispensers/Repuestos que sí están "
        "explícitamente fuera de alcance de D1.",
    )
    linea_ids = fields.One2many(
        comodel_name="insa.control.playa.linea",
        inverse_name="control_playa_id",
        string="Líneas",
    )
    aprobado_por_id = fields.Many2one(
        comodel_name="res.users",
        string="Aprobado por (controlador)",
        readonly=True,
        copy=False,
        help="C-08 (confirmado en la reunión del 08/06; antes era el "
        "supuesto 0.1): segundo factor de aprobación, usuario/contraseña "
        "real de Odoo con el grupo insa_reparto.group_controlador. Se "
        "completa vía POST /insa/api/control-playa/aprobar desde la app, o "
        "con el wizard de aprobación desde el backoffice (C-09, el fallback "
        "para cuando la app falla). Nunca a mano.",
    )
    aprobado_fecha = fields.Datetime(string="Fecha de aprobación", readonly=True, copy=False)
    reabierto_para_correccion = fields.Boolean(
        string="Reabierto para corrección",
        readonly=True,
        copy=False,
        help="R-06: el control ya había sido aprobado y se volvió a "
        "borrador para corregirlo desde la web. Solo en este caso la "
        "administrativa de cierre lo puede volver a confirmar sin las "
        "credenciales de un controlador.",
    )

    _sql_constraints = [
        (
            "hoja_ruta_tipo_unique",
            "unique(hoja_ruta_id, tipo)",
            "Ya existe un control de playa de este tipo para esta hoja de ruta "
            "(RN-04: la liquidación asume un único control de salida y uno de "
            "entrada por reparto).",
        ),
    ]

    # Campos que el controlador todavía puede tocar con el control ya
    # confirmado: son metadatos de la aprobación misma, no el conteo.
    _CAMPOS_LIBRES_CONFIRMADO = {
        "state",
        "aprobado_por_id",
        "aprobado_fecha",
        "reabierto_para_correccion",
    }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.tipo == "salida" and not rec.linea_ids:
                rec._precargar_pedido_habitual()
            elif rec.tipo == "entrada" and not rec.linea_ids:
                rec._precargar_desde_salida()
        return records

    def _precargar_pedido_habitual(self):
        """4.4 (revisión 29/09): al crear el control de SALIDA, arranca con
        la suma del pedido habitual (4.3) de los clientes de la hoja, por
        producto — el repartidor/controlador ajusta cantidad (Llenos)
        desde ahí si carga más (ver cantidad_extra en la línea). Revisión
        07/10: un cliente con abono activo usa la cantidad por visita del
        abono en vez del pedido habitual manual para esos productos (ver
        res.partner._pedido_habitual_efectivo)."""
        self.ensure_one()
        partners = self.hoja_ruta_id.order_ids.mapped("location_id.partner_id")
        if not partners:
            return
        por_producto = {}
        for partner in partners:
            for producto, cantidad in partner._pedido_habitual_efectivo().items():
                if not producto.visible_control_playa:
                    continue
                por_producto[producto] = por_producto.get(producto, 0.0) + cantidad
        self.env["insa.control.playa.linea"].create(
            [
                {
                    "control_playa_id": self.id,
                    "product_id": producto.id,
                    "cantidad": cantidad,
                    "a_cargar_pedidos": cantidad,
                }
                for producto, cantidad in por_producto.items()
            ]
        )

    def _precargar_desde_salida(self):
        """Revisión 05/10: al crear el control de ENTRADA, precarga un
        producto por cada uno que salió en el control de SALIDA de la
        misma hoja (habitual + extra, lo que sea que haya salido con
        cantidad) — así el controlador solo completa Llenos/Vacíos/
        Fallados en vez de buscar cada producto de nuevo."""
        self.ensure_one()
        salida = self.hoja_ruta_id.control_salida_ids[:1]
        productos = salida.linea_ids.filtered(lambda l: l.cantidad).mapped("product_id")
        if not productos:
            return
        self.env["insa.control.playa.linea"].create(
            [
                {"control_playa_id": self.id, "product_id": producto.id}
                for producto in productos
            ]
        )

    def write(self, vals):
        # C-08: "el controlador puede corregir las cantidades ANTES de
        # aprobar" — después, no. El conteo aprobado es el documento
        # contra el que se liquida el día; si se pudiera editar, la firma
        # del controlador no valdría nada. Para corregir hay que volver a
        # borrador (action_volver_a_borrador: supervisor o administrativa
        # de cierre, R-06).
        if not self.env.context.get("insa_aprobacion"):
            for rec in self:
                if rec.state == "confirmado" and set(vals) - self._CAMPOS_LIBRES_CONFIRMADO:
                    raise UserError(
                        _(
                            "El control de playa de %s ya fue aprobado por %s: "
                            "no se puede modificar. Un supervisor o la "
                            "administrativa de cierre pueden volverlo a "
                            "borrador para corregirlo."
                        )
                        % (rec.tipo, rec.aprobado_por_id.name or "-")
                    )
        return super().write(vals)

    def action_aprobar(self, login, password):
        """RN-22: ningún control de playa queda confirmado sin la
        aprobación de un usuario con permiso de Controlador de Playa,
        autenticado en el momento.

        Punto de entrada único de la aprobación: lo usan tanto
        POST /insa/api/control-playa/aprobar (la app, el 99% de los casos:
        el repartidor le pasa el teléfono al controlador, que tipea sus
        credenciales sin cerrar la sesión del repartidor) como el wizard
        del backoffice (C-09, el fallback para cuando la app falla).

        Usa res.users.authenticate(), no execute_kw ni sesión Odoo: valida
        la contraseña y devuelve el uid, sin dar acceso genérico a Odoo.
        """
        self.ensure_one()
        try:
            auth_info = self.env["res.users"].sudo().authenticate(
                self.env.cr.dbname,
                {"type": "password", "login": login, "password": password},
                {"interactive": False},
            )
        except AccessDenied as exc:
            raise UserError(_("Usuario o contraseña incorrectos.")) from exc

        controlador = self.env["res.users"].sudo().browse(auth_info["uid"])
        return self._aprobar_como(controlador)

    def _aprobar_como(self, controlador):
        """La aprobación en sí, con el controlador ya identificado.
        Separado de action_aprobar porque res.users.authenticate() abre su
        propio cursor: dentro de un test no ve los usuarios creados en la
        transacción, así que la suite entra por acá y el chequeo de
        permiso se sigue ejecutando igual."""
        self.ensure_one()
        if self.state == "confirmado":
            raise UserError(
                _("El control de playa de %s ya está aprobado.") % self.tipo
            )
        if not self.linea_ids:
            raise UserError(
                _("No se puede aprobar un control de playa sin líneas cargadas.")
            )
        if self.tipo == "entrada" and not self.hoja_ruta_id.control_salida_aprobado:
            raise UserError(
                _(
                    "No se puede aprobar el control de entrada de un reparto "
                    "cuya salida todavía no fue aprobada."
                )
            )
        if not controlador.has_group("insa_reparto.group_controlador"):
            raise UserError(
                _(
                    "%s no tiene permiso de Controlador de Playa: no puede "
                    "aprobar un control. El repartidor no puede aprobar su "
                    "propio conteo."
                )
                % controlador.name
            )

        self.with_context(insa_aprobacion=True).write(
            {
                "state": "confirmado",
                "aprobado_por_id": controlador.id,
                "aprobado_fecha": fields.Datetime.now(),
                "reabierto_para_correccion": False,
            }
        )
        self._recalcular_liquidacion_si_corresponde()
        return True

    def _recalcular_liquidacion_si_corresponde(self):
        """RN-23: "recién ahí [con la entrada aprobada] la liquidación nos
        arroja cómo quedaron las ventas y las devoluciones". R-04: la
        liquidación es la parte del controlador de playa, así que queda
        calculada en el momento en que aprueba el regreso — no espera a
        que la administrativa declare el efectivo."""
        for rec in self:
            hoja = rec.hoja_ruta_id
            if hoja.state != "cerrada" and hoja.control_entrada_aprobado:
                hoja.action_recalcular_liquidacion()

    def _resumen_conteo(self):
        self.ensure_one()
        return "; ".join(
            "%s: %g llenos / %g vacíos / %g fallados"
            % (linea.product_id.name, linea.cantidad, linea.cantidad_vacia, linea.cantidad_fallada)
            for linea in self.linea_ids
        ) or _("sin líneas")

    def action_abrir_wizard_aprobar(self):
        """C-09: aprobación desde el backoffice web. 'Si por algún motivo
        algo falló, lo pueden hacer esas personas autorizadas desde la
        web' — mismo segundo factor que en la app, no un atajo."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Aprobar control de playa"),
            "res_model": "insa.aprobar.control.playa.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_control_playa_id": self.id},
        }

    def action_volver_a_borrador(self):
        """Deshacer una aprobación para poder corregir el conteo: es romper
        la firma del controlador. Supervisor, o la administrativa de
        cierre (R-06: "suele pasar bastante que hay algún error en el
        control de playa... la administrativa de la tarde puede
        modificar desde la web"). El conteo de antes queda en el chatter
        de la hoja de ruta."""
        if not (
            self.env.user.has_group("insa_reparto.group_supervisor")
            or self.env.user.has_group("insa_reparto.group_administrativa_cierre")
        ):
            raise UserError(
                _(
                    "Solo un supervisor o la administrativa de cierre pueden "
                    "reabrir un control de playa aprobado."
                )
            )
        for rec in self:
            if rec.hoja_ruta_id.state == "cerrada":
                raise UserError(
                    _("La hoja de ruta ya está cerrada: el control no se puede reabrir.")
                )
            rec.hoja_ruta_id._message_log(
                body=_(
                    "Control de %(tipo)s reabierto para corrección por %(user)s "
                    "(lo había aprobado %(aprobo)s). Conteo antes: %(conteo)s"
                )
                % {
                    "tipo": rec.tipo,
                    "user": self.env.user.name,
                    "aprobo": rec.aprobado_por_id.name or "-",
                    "conteo": rec._resumen_conteo(),
                }
            )
            rec.with_context(insa_aprobacion=True).write(
                {
                    "state": "borrador",
                    "aprobado_por_id": False,
                    "aprobado_fecha": False,
                    "reabierto_para_correccion": True,
                }
            )
        return True

    def action_confirmar_correccion(self):
        """R-06: la administrativa de cierre vuelve a confirmar el control
        que ella misma reabrió y corrigió, sin pedir las credenciales de
        un controlador — solo para controles que ya habían sido aprobados
        (reabierto_para_correccion); la primera aprobación sigue siendo
        del controlador de playa (C-08). La liquidación se recalcula."""
        if not self.env.user.has_group("insa_reparto.group_administrativa_cierre"):
            raise UserError(
                _("Solo la administrativa de cierre puede confirmar una corrección.")
            )
        for rec in self:
            if rec.state != "borrador" or not rec.reabierto_para_correccion:
                raise UserError(
                    _(
                        "Solo se confirma como corrección un control aprobado "
                        "que se reabrió para corregirlo."
                    )
                )
            if not rec.linea_ids:
                raise UserError(
                    _("No se puede confirmar un control de playa sin líneas cargadas.")
                )
            rec.with_context(insa_aprobacion=True).write(
                {
                    "state": "confirmado",
                    "aprobado_por_id": self.env.user.id,
                    "aprobado_fecha": fields.Datetime.now(),
                    "reabierto_para_correccion": False,
                }
            )
            rec.hoja_ruta_id._message_log(
                body=_("Corrección del control de %(tipo)s confirmada por %(user)s. Conteo después: %(conteo)s")
                % {"tipo": rec.tipo, "user": self.env.user.name, "conteo": rec._resumen_conteo()}
            )
        self._recalcular_liquidacion_si_corresponde()
        return True


class InsaControlPlayaLinea(models.Model):
    _name = "insa.control.playa.linea"
    _description = "Línea de control de playa"

    control_playa_id = fields.Many2one(
        comodel_name="insa.control.playa", required=True, ondelete="cascade"
    )
    tipo = fields.Selection(related="control_playa_id.tipo")
    # Revisión 05/10: en la línea de ENTRADA, referencia de solo lectura
    # de cuánto salió de este producto en el control de SALIDA de la
    # misma hoja — "para ya tener una primera comparativa" al cargar lo
    # que vuelve.
    cantidad_salida = fields.Float(
        string="Salió",
        compute="_compute_cantidad_salida",
        help="Cantidad de este producto en el control de SALIDA de la "
        "misma hoja de ruta. Solo de referencia, no se edita acá.",
    )

    @api.depends(
        "tipo",
        "product_id",
        "control_playa_id.hoja_ruta_id.control_salida_ids.linea_ids.product_id",
        "control_playa_id.hoja_ruta_id.control_salida_ids.linea_ids.cantidad",
    )
    def _compute_cantidad_salida(self):
        for rec in self:
            if rec.tipo != "entrada":
                rec.cantidad_salida = 0.0
                continue
            salida = rec.control_playa_id.hoja_ruta_id.control_salida_ids[:1]
            linea_salida = salida.linea_ids.filtered(
                lambda l: l.product_id == rec.product_id
            )[:1]
            rec.cantidad_salida = linea_salida.cantidad

    product_id = fields.Many2one(
        comodel_name="product.template",
        string="Producto",
        required=True,
        domain=[("visible_control_playa", "=", True)],
    )
    # C-05 / RN-21: el control de playa maneja TRES cantidades por
    # producto, no dos. La v1 modelaba llenos/vacíos y asumía salida =
    # todo lleno; la reunión del 08/06 lo corrige ("en el control de
    # salida vos tenés llenos, vacíos y fallados").
    #
    # Los nombres internos quedan como estaban (cantidad / cantidad_vacia)
    # a propósito: el módulo ya está instalado y renombrar una columna
    # pierde los conteos cargados. Lo que se alinea con el vocabulario de
    # INSA son las etiquetas visibles — Llenos / Vacíos / Fallados.
    cantidad = fields.Float(
        string="Llenos",
        help="Producto lleno. En salida es toda la carga del camión; en "
        "entrada, lo que volvió sin vender.",
    )
    cantidad_vacia = fields.Float(
        string="Vacíos",
        help="Envase vacío que volvió del reparto. En salida siempre 0 "
        "(nunca se sale a repartir con un producto vacío).",
    )
    cantidad_fallada = fields.Float(
        string="Fallados",
        help="Producto NO CONFORME que volvió: pinchado, hinchado o con "
        "falla de calidad. Cuenta para el saldo de envases (el envase "
        "volvió) pero no reingresa a stock vendible. En salida siempre 0. "
        "Se concilia contra las devoluciones cargadas en las visitas "
        "(C-07).",
    )
    # 4.4 (revisión 29/09): precarga desde el pedido habitual (4.3) de los
    # clientes de la hoja. a_cargar_pedidos es la referencia fija del
    # momento en que se creó la línea; cantidad_extra es lo que cantidad
    # (Llenos, editable) tiene de más sobre esa referencia — nunca
    # modifica cantidad, que sigue siendo lo único que vale para la
    # liquidación.
    a_cargar_pedidos = fields.Float(
        string="A cargar (pedidos)",
        readonly=True,
        help="Suma del pedido habitual de los clientes de esta hoja para "
        "este producto, calculada al crear la línea.",
    )
    cantidad_extra = fields.Float(
        string="Extra",
        compute="_compute_cantidad_extra",
        help="cantidad (Llenos) por encima de lo que pedían los clientes "
        "del día — lo que se suma por si surge una venta espontánea.",
    )

    @api.depends("cantidad", "a_cargar_pedidos")
    def _compute_cantidad_extra(self):
        for rec in self:
            rec.cantidad_extra = max(rec.cantidad - rec.a_cargar_pedidos, 0.0)

    @api.constrains("cantidad", "a_cargar_pedidos")
    def _check_salida_cubre_pedidos(self):
        # Revisión 06/10: "una restricción que no me permita poner un
        # valor menor del que muestra el campo de 'a cargar pedidos'...
        # así puedo colocar más productos si quiero pero no menos, para
        # poder tener para realizar los pedidos de los clientes". Solo en
        # salida: a_cargar_pedidos queda siempre en 0 en una línea de
        # entrada (ver _precargar_desde_salida), así que ahí la condición
        # nunca se dispara igual, pero se acota por las dudas.
        for rec in self:
            if rec.tipo != "salida":
                continue
            if rec.cantidad < rec.a_cargar_pedidos:
                raise ValidationError(
                    _(
                        "%(producto)s: hay que cargar al menos %(pedido)g "
                        "(lo que piden los clientes de hoy). Cargaste "
                        "%(cargado)g."
                    )
                    % {
                        "producto": rec.product_id.name,
                        "pedido": rec.a_cargar_pedidos,
                        "cargado": rec.cantidad,
                    }
                )

    @api.constrains("cantidad_vacia", "cantidad_fallada")
    def _check_salida_solo_llenos(self):
        # RN-21: en salida, vacíos y fallados son siempre 0. Desde la
        # reunión del 14/09 (R-05) la vista de salida ni siquiera los
        # muestra ("en la salida solo sale con mercadería llena"); la
        # validación queda igual para lo que entre por la API.
        # 'tipo' es un related no-stored (viene de control_playa_id.tipo):
        # no puede ir en @api.constrains (Odoo tira warning, "not
        # writeable"), pero sí se puede leer adentro sin problema.
        for rec in self:
            if rec.tipo != "salida":
                continue
            if rec.cantidad_vacia:
                raise ValidationError(
                    _(
                        "%s: un control de playa de SALIDA no registra "
                        "cantidad vacía — nunca se sale a repartir con un "
                        "producto vacío. Dejalo en 0."
                    )
                    % rec.product_id.name
                )
            if rec.cantidad_fallada:
                raise ValidationError(
                    _(
                        "%s: un control de playa de SALIDA no registra "
                        "producto fallado — el fallado es lo que vuelve del "
                        "reparto, no lo que se lleva. Dejalo en 0."
                    )
                    % rec.product_id.name
                )

    @api.constrains("cantidad", "cantidad_vacia", "cantidad_fallada")
    def _check_cantidades_no_negativas(self):
        for rec in self:
            if rec.cantidad < 0 or rec.cantidad_vacia < 0 or rec.cantidad_fallada < 0:
                raise ValidationError(
                    _("%s: las cantidades de un control de playa no pueden ser negativas.")
                    % rec.product_id.name
                )

    def _check_control_editable(self):
        # Mismo bloqueo que insa.control.playa.write(), pero por línea: sin
        # esto se podría editar el conteo de un control ya aprobado
        # entrando por la línea en vez de por la cabecera.
        for rec in self:
            if rec.control_playa_id.state == "confirmado":
                raise UserError(
                    _(
                        "El control de playa de %s ya fue aprobado: no se "
                        "pueden modificar sus líneas."
                    )
                    % rec.control_playa_id.tipo
                )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_control_editable()
        return records

    def write(self, vals):
        self._check_control_editable()
        return super().write(vals)

    def unlink(self):
        self._check_control_editable()
        return super().unlink()


class InsaAprobarControlPlayaWizard(models.TransientModel):
    """C-09: el fallback web de la aprobación del control de playa. El
    99% de las aprobaciones se hace desde la app (el repartidor le pasa
    el teléfono al controlador), pero 'si por algún motivo algo falló, lo
    pueden hacer esas personas autorizadas desde la web'."""

    _name = "insa.aprobar.control.playa.wizard"
    _description = "Aprobar control de playa (credenciales del controlador)"

    control_playa_id = fields.Many2one(
        comodel_name="insa.control.playa", required=True, readonly=True
    )
    login = fields.Char(string="Usuario del controlador", required=True)
    password = fields.Char(string="Contraseña", required=True)

    def action_confirmar(self):
        self.ensure_one()
        self.control_playa_id.action_aprobar(self.login, self.password)
        return {"type": "ir.actions.act_window_close"}
