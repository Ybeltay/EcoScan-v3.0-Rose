// ── BASE URL (dinámica: local vs producción) ──────────────────────────────────
// La página y la API las sirve el mismo servidor, así que siempre se usa el mismo origen
// (con "file://" no hay servidor: se cae a localhost por si abres el HTML a mano).
const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:5050' : window.location.origin;

// ── Mensaje único para fallos de conexión con la IA (idéntico al del servidor) ──
const CONN_TITLE = 'Fallo de conexión con la IA';
const CONN_HINT  = 'Verifica tu conexión a internet e inténtalo de nuevo.';
const CONN_MSG   = CONN_TITLE + '. ' + CONN_HINT;

// ── Otro caso distinto: el propio servidor de EcoScan dejó de responder (p. ej. la ventana negra se cerró o el proceso cayó) ──
const SERVER_TITLE = 'Se perdió la conexión con el servidor de EcoScan';
const SERVER_HINT  = 'Si lo ejecutas en tu equipo, revisa que la ventana negra de iniciar_ecoscan.bat siga abierta; si se cerró, ábrelo de nuevo y repite el análisis.';
const SERVER_MSG   = SERVER_TITLE + '. ' + SERVER_HINT;

// ── v3.7 · Procedencia de los datos: qué es medición, qué es modelo y qué es estimación ──
const PROV = {
    lab:      ['🔬', 'Laboratorio'],
    field:    ['🧪', 'Medido en campo'],
    test:     ['🧫', 'Prueba de campo'],
    nearby:   ['📍', 'Perfil cercano'],
    modeled:  ['🛰️', 'Modelado satelital'],
    declared: ['✍️', 'Declarado'],
    ai:       ['🤖', 'Estimado por IA'],
    ref:      ['📚', 'Referencia'],
    none:     ['❔', 'Sin medición']
};
const PROV_PLAIN = { lab: 'Laboratorio', field: 'Medido en campo', test: 'Prueba de campo', nearby: 'Perfil cercano',
                     modeled: 'Modelado (satélite)', declared: 'Declarado', ai: 'Estimado por IA', ref: 'Referencia', none: 'Sin medición' };
const provChip = (code, label) => {
    const k = PROV[code] ? code : 'none';
    return `<span class="prov prov-${k}">${PROV[k][0]} ${label || PROV[k][1]}</span>`;
};

// ── KIT DE MAQUETACIÓN PARA LOS PDF (jsPDF) ─────────────────────────────────
// Cada LÍNEA de texto comprueba el salto de página por sí sola: ningún párrafo,
// tarjeta ni tabla puede salirse del papel ni quedar debajo del pie de página
// (antes un párrafo largo solo comprobaba el salto una vez y se perdía el resto).
// Los dos PDF (suelo y planta) usan este mismo kit, así que se ven iguales.
function makePdfKit(doc, { footerLabel, fecha }) {
    const PW = 210, PH = 297, ML = 16, CW = PW - ML * 2, TOP = 18, BOT = PH - 20;
    const C = {
        dark: [30, 90, 40], med: [76, 175, 80], light: [232, 243, 233], txt: [40, 40, 40], mut: [100, 100, 100],
        bg: [245, 247, 245], white: [255, 255, 255], line: [208, 224, 208], warn: [180, 100, 0], bad: [180, 40, 40],
        info: [30, 100, 160], fix: [30, 100, 40], tint: [236, 246, 238]
    };
    const PT = 0.3528;   // milímetros por punto tipográfico
    // La fuente estándar del PDF solo dibuja Latin-1: se limpian emojis y símbolos ANTES de medir y partir el texto
    const T = (s) => String(s ?? '')
        .replace(/[→⇒]/g, '->').replace(/≥/g, '>=').replace(/≤/g, '<=').replace(/≈/g, '~').replace(/₂/g, '2').replace(/σ/g, 's')
        .replace(/[^\u0020-\u007E\u00A0-\u00FF\u2013\u2014\u2018\u2019\u201C\u201D\u2022\u2026]/g, ' ').replace(/\s+/g, ' ').trim();
    const k = { y: TOP, n: 0 };
    const lh = (size, f = 1.38) => size * PT * f;          // alto de una línea (mm)
    const base = (size) => size * PT * 0.945;              // línea base dentro de su caja
    const font = (style, size, color) => { doc.setFont('helvetica', style); doc.setFontSize(size); doc.setTextColor(...color); };
    const rect = (x, y, w, h, r, fill) => { doc.setFillColor(...fill); doc.roundedRect(x, y, w, h, r, r, 'F'); };
    const ensure = (h) => { if (k.y + h > BOT) { doc.addPage(); k.y = TOP; return true; } return false; };
    const wrap = (text, w, style = 'normal', size = 9.5) => {
        const t = T(text);
        if (!t) return [];
        font(style, size, C.txt);
        return doc.splitTextToSize(t, w);
    };
    // Una sola línea que nunca desborda su ancho (añade «…» si hace falta)
    const fit = (text, w, style = 'normal', size = 9) => {
        let s = T(text);
        font(style, size, C.txt);
        if (doc.getTextWidth(s) <= w) return s;
        while (s.length > 1 && doc.getTextWidth(s + '…') > w) s = s.slice(0, -1);
        return s + '…';
    };

    const section = (title, minAfter = 16) => {
        ensure(11 + 4 + minAfter);          // nunca un título solo al final de la página
        k.n += 1;
        rect(ML, k.y, CW, 10, 2, C.light);
        doc.setFillColor(...C.dark); doc.circle(ML + 6, k.y + 5, 3.4, 'F');
        font('bold', 9, C.white); doc.text(String(k.n), ML + 6, k.y + 6.3, { align: 'center' });
        font('bold', 11, C.dark); doc.text(T(title), ML + 13, k.y + 6.8);
        k.y += 14.5;
    };
    const sub = (text) => {
        ensure(16);
        doc.setFillColor(...C.med); doc.rect(ML + 1, k.y + 0.6, 1, 4, 'F');
        font('bold', 9.5, C.dark); doc.text(T(text), ML + 4.5, k.y + 3.9);
        k.y += 6.5;
    };
    const gap = (mm) => { k.y += mm; };

    const para = (text, o = {}) => {
        const { size = 9.5, color = C.txt, indent = 0, style = 'normal', after = 2 } = o;
        const lines = wrap(text, CW - indent - 1, style, size);
        if (!lines.length) return;
        const h = lh(size);
        ensure(h * Math.min(lines.length, 2));         // no deja una sola línea huérfana
        lines.forEach((ln) => { ensure(h); font(style, size, color); doc.text(ln, ML + indent, k.y + base(size)); k.y += h; });
        k.y += after;
    };

    const kv = (label, value, o = {}) => {
        const { labelW = 38, size = 9.3, color = C.txt } = o;
        const v0 = String(value ?? '').trim();
        const lines = wrap(v0.charAt(0).toUpperCase() + v0.slice(1), CW - labelW - 1, 'normal', size);
        if (!lines.length) return;
        const h = lh(size);
        ensure(h * Math.min(lines.length, 2));
        lines.forEach((ln, i) => {
            ensure(h);
            if (i === 0) { font('bold', size, C.dark); doc.text(fit(label, labelW - 2, 'bold', size), ML + 1, k.y + base(size)); }
            font('normal', size, color); doc.text(ln, ML + labelW, k.y + base(size));
            k.y += h;
        });
        k.y += 1.3;
    };

    const bullets = (items, o = {}) => {
        const { size = 9.2, color = C.txt, indent = 2 } = o;
        const h = lh(size);
        (items || []).filter(Boolean).forEach((it) => {
            const lines = wrap(it, CW - indent - 6, 'normal', size);
            if (!lines.length) return;
            ensure(h * Math.min(lines.length, 2));
            lines.forEach((ln, i) => {
                ensure(h);
                if (i === 0) { doc.setFillColor(...C.med); doc.circle(ML + indent + 1.2, k.y + h * 0.5, 0.75, 'F'); }
                font('normal', size, color); doc.text(ln, ML + indent + 4.5, k.y + base(size));
                k.y += h;
            });
            k.y += 1;
        });
        k.y += 1;
    };

    // Tarjeta con título, etiqueta a la derecha y bloques de texto. Si cabe en una página se mantiene entera.
    const card = ({ title, tag, tagColor = C.med, meta, blocks = [], fill = C.bg, accent = C.med }) => {
        const PADX = 4.5, PADY = 2.6, tH = lh(9.8, 1.3), mH = lh(8.2, 1.3);
        let tagW = 0;
        if (tag) { font('bold', 7.6, C.white); tagW = doc.getTextWidth(T(tag)) + 5; }
        const tLines = wrap(title, CW - PADX * 2 - (tagW ? tagW + 3 : 0), 'bold', 9.8);
        const mLines = meta ? wrap(meta, CW - PADX * 2, 'normal', 8.2) : [];
        const bs = blocks.map((b) => { const size = b.size || 8.8; return { ...b, size, lines: wrap(b.text, CW - PADX * 2, b.style || 'normal', size) }; }).filter((b) => b.lines.length);
        const H = PADY * 2 + tLines.length * tH + mLines.length * mH + bs.reduce((s, b) => s + b.lines.length * lh(b.size, 1.3) + 1, 0);
        if (H > BOT - TOP - 4) {              // tarjeta gigante: se dibuja como flujo normal
            para(title, { style: 'bold' });
            if (meta) para(meta, { size: 8.2, color: C.mut });
            bs.forEach((b) => para(b.text, { size: b.size, color: b.color || C.txt }));
            return;
        }
        ensure(H + 2.5);
        rect(ML, k.y, CW, H, 2.5, fill);
        doc.setFillColor(...accent); doc.roundedRect(ML, k.y, 1.6, H, 0.8, 0.8, 'F');
        let y = k.y + PADY;
        font('bold', 9.8, C.txt);
        tLines.forEach((ln) => { doc.text(ln, ML + PADX, y + base(9.8)); y += tH; });
        if (tag) {
            rect(ML + CW - PADX - tagW, k.y + PADY - 0.2, tagW, 4.8, 2, tagColor);
            font('bold', 7.6, C.white); doc.text(T(tag), ML + CW - PADX - tagW / 2, k.y + PADY + 3.2, { align: 'center' });
        }
        mLines.forEach((ln) => { font('normal', 8.2, C.mut); doc.text(ln, ML + PADX, y + base(8.2)); y += mH; });
        bs.forEach((b) => {
            const h = lh(b.size, 1.3);
            b.lines.forEach((ln) => { font(b.style || 'normal', b.size, b.color || C.mut); doc.text(ln, ML + PADX, y + base(b.size)); y += h; });
            y += 1;
        });
        k.y += H + 2.5;
    };

    // Etiquetas en fila (se parten en varias líneas si no caben)
    const chips = (items) => {
        let x = ML + 1;
        ensure(7);
        (items || []).filter((c) => c && c.t).forEach((c) => {
            const t = T(c.t);
            if (!t) return;
            font('bold', 7.8, C.white);
            const w = doc.getTextWidth(t) + 5;
            if (x + w > ML + CW) { x = ML + 1; k.y += 6.8; ensure(6.8); }
            rect(x, k.y, w, 5.2, 2.2, c.color || C.med);
            doc.text(t, x + 2.5, k.y + 3.7);
            x += w + 2;
        });
        k.y += 8.5;
    };

    // Tabla con encabezado que se repite al saltar de página y filas que se ajustan al texto
    const table = (cols, rows) => {
        const size = 8.2, h = lh(size, 1.3), PADX = 2;
        const xs = []; let x = ML;
        cols.forEach((c) => { xs.push(x); x += c.w; });
        const header = () => {
            ensure(7.4);
            rect(ML, k.y, CW, 6.4, 1.8, C.light);
            font('bold', 7.8, C.dark);
            cols.forEach((c, i) => doc.text(T(c.label), xs[i] + PADX, k.y + 4.4));
            k.y += 7.2;
        };
        header();
        rows.forEach((r, ri) => {
            const cells = r.map((c, i) => {
                const o = (c && typeof c === 'object') ? c : { t: c };
                return { ...o, lines: wrap(o.t, cols[i].w - PADX * 2, o.bold ? 'bold' : 'normal', size) };
            });
            const n = Math.max(1, ...cells.map((c) => c.lines.length));
            const H = n * h + 2.4;
            if (k.y + H > BOT) { doc.addPage(); k.y = TOP; header(); }
            if (ri % 2 === 0) rect(ML, k.y, CW, H, 1.2, C.bg);
            cells.forEach((c, i) => {
                font(c.bold ? 'bold' : 'normal', size, c.color || C.txt);
                c.lines.forEach((ln, li) => doc.text(ln, xs[i] + PADX, k.y + 1.2 + li * h + base(size)));
            });
            k.y += H;
        });
        k.y += 2.5;
    };

    const hr = () => { ensure(5); doc.setDrawColor(...C.line); doc.setLineWidth(0.3); doc.line(ML, k.y + 1, ML + CW, k.y + 1); k.y += 4; };

    // Pie de página en todas las páginas (se llama una vez, al final)
    const footers = () => {
        const total = doc.getNumberOfPages();
        for (let i = 1; i <= total; i++) {
            doc.setPage(i);
            doc.setFillColor(...C.dark); doc.rect(0, PH - 14, PW, 14, 'F');
            font('normal', 7.5, C.white);
            doc.text(T(footerLabel), ML, PH - 6);
            doc.text(T(fecha), PW / 2, PH - 6, { align: 'center' });
            doc.text('Página ' + i + ' de ' + total, PW - ML, PH - 6, { align: 'right' });
        }
        return total;
    };

    // Brote dibujado con formas vectoriales (un emoji no se vería con la fuente del PDF)
    const sprout = (cx, cy, scale = 1) => {
        doc.setFillColor(...C.white);
        doc.rect(cx - 0.55 * scale, cy - 2 * scale, 1.1 * scale, 9 * scale, 'F');
        doc.ellipse(cx - 4 * scale, cy - 1.5 * scale, 4 * scale, 2.4 * scale, 'F');
        doc.ellipse(cx + 4 * scale, cy - 4.5 * scale, 4 * scale, 2.4 * scale, 'F');
    };

    // Banda verde de portada con logo, nombre y subtítulo
    const banner = (subtitle) => {
        doc.setFillColor(...C.dark); doc.rect(0, 0, PW, 54, 'F');
        doc.setFillColor(...C.med); doc.circle(PW / 2, 21, 13, 'F');
        doc.setDrawColor(...C.white); doc.setLineWidth(0.5); doc.circle(PW / 2, 21, 10.2, 'S');
        sprout(PW / 2, 23.5, 0.8);
        font('bold', 24, C.white); doc.text('EcoScan', PW / 2, 42, { align: 'center' });
        font('normal', 10.5, [200, 230, 200]); doc.text(T(subtitle), PW / 2, 49.5, { align: 'center' });
        k.y = 62;
    };

    return { C, T, k, PW, PH, ML, CW, TOP, BOT, lh, base, font, rect, ensure, wrap, fit, section, sub, gap, para, kv, bullets, card, chips, table, hr, footers, banner };
}

// ── SALUDO CAMBIANTE DEL INICIO ──────────────────────────────────────────────
// Catálogo de frases. Cada una tiene:
//   c    tema: 'planta' · 'suelo' · 'charla'
//   t    texto. {n} = tu primer nombre (si no hay nombre, se omite junto con su coma) · {saludo} = Buenos días / Buenas tardes / Buenas noches
//   when (opcional) franjas del día en que aplica. Las frases con when o con {saludo} son «de la hora»: solo salen de vez en cuando.
// Franjas: madrugada · amanecer (mañana temprano) · mañana (media mañana) · mediodia · tarde · anochecer · noche
const GREET_PERIODS = [
    { id: 'madrugada', from: 0,  to: 5  },
    { id: 'amanecer',  from: 5,  to: 9  },
    { id: 'mañana',    from: 9,  to: 12 },
    { id: 'mediodia',  from: 12, to: 15 },
    { id: 'tarde',     from: 15, to: 19 },
    { id: 'anochecer', from: 19, to: 21 },
    { id: 'noche',     from: 21, to: 24 },
];
// Probabilidad de que el titular / cada línea de apoyo sea «de la hora». El resto del tiempo sale una frase general.
// Nunca salen dos «de la hora» seguidas (el titular cuenta como la anterior).
const GREET_TIME_CHANCE = 0.3;
const GREET_TIP_TIME_CHANCE = 0.2;
// Cuántas frases recientes se recuerdan para no repetirlas (se guardan en este navegador)
const GREET_HISTORY = 60;

const GREET_HEADLINES = [
    // ── generales · charla ──
    { c: 'charla', t: 'Qué bueno verte, {n}.' },
    { c: 'charla', t: 'Hola de nuevo, {n}. Aquí sigo.' },
    { c: 'charla', t: 'Cuando tú digas, {n}.' },
    { c: 'charla', t: 'Hoy es buen día para mirar de cerca, {n}.' },
    { c: 'charla', t: '¿Por dónde empezamos, {n}?' },
    { c: 'charla', t: 'Sin prisa, {n}. La tierra tampoco la tiene.' },
    { c: 'charla', t: 'Me da gusto tenerte por aquí, {n}.' },
    { c: 'charla', t: '¿Qué traes entre manos hoy, {n}?' },
    { c: 'charla', t: 'Aquí estoy para lo que necesites, {n}.' },
    { c: 'charla', t: 'Vamos con calma y con buenos ojos, {n}.' },
    { c: 'charla', t: '¿Listo para otra revisión, {n}?' },
    { c: 'charla', t: 'Cuéntame qué estás viendo, {n}.' },
    { c: 'charla', t: 'Qué gusto que vuelvas, {n}.' },
    { c: 'charla', t: 'Tú pones la foto y yo pongo el análisis, {n}.' },
    { c: 'charla', t: 'Lo verde siempre tiene una historia, {n}.' },
    { c: 'charla', t: 'Hoy toca observar un poco, {n}.' },
    { c: 'charla', t: '¿Hay algo que te preocupe, {n}? Revisémoslo juntos.' },
    { c: 'charla', t: 'Un diagnóstico a la vez, {n}.' },
    { c: 'charla', t: 'Pregunta lo que quieras, {n}.' },
    { c: 'charla', t: 'Todo gran jardín empezó con una duda, {n}.' },
    { c: 'charla', t: '¿Qué te trae por aquí hoy, {n}?' },
    { c: 'charla', t: 'Con un poco de atención se arregla mucho, {n}.' },
    { c: 'charla', t: 'Gracias por cuidar lo que cuidas, {n}.' },
    { c: 'charla', t: 'Aquí no hay prisa ni juicios, {n}.' },
    { c: 'charla', t: 'Hoy también hay algo que aprender, {n}.' },
    { c: 'charla', t: 'Qué bueno que estés de vuelta.' },
    { c: 'charla', t: 'Escanear es fácil. Entender es lo bonito.' },
    { c: 'charla', t: 'Planta o suelo: tú eliges, {n}.' },
    { c: 'charla', t: 'Empecemos por lo que más te inquieta, {n}.' },
    { c: 'charla', t: 'La curiosidad es el mejor fertilizante, {n}.' },
    { c: 'charla', t: 'Lo que se observa, se cuida, {n}.' },
    { c: 'charla', t: 'Un buen cuidador empieza por observar, {n}.' },
    { c: 'charla', t: 'Dos caminos y ningún error posible, {n}.' },
    { c: 'charla', t: 'Tu jardín te escucha, {n}. Y yo también.' },
    { c: 'charla', t: 'Respira, {n}. Lo verde se arregla con paciencia.' },
    // ── generales · plantas ──
    { c: 'planta', t: '¿Cómo están tus plantas hoy, {n}?' },
    { c: 'planta', t: 'Tus plantas hablan con las hojas, {n}. Vamos a escucharlas.' },
    { c: 'planta', t: '¿Una hoja rara, {n}? Enséñamela.' },
    { c: 'planta', t: '¿Una planta misteriosa, {n}? Vamos a ponerle nombre.' },
    { c: 'planta', t: '¿Hojas amarillas, manchas o plagas? Averigüémoslo, {n}.' },
    { c: 'planta', t: 'Que a tu planta no se le marchite el día, {n}.' },
    { c: 'planta', t: 'Una foto de cerca y tu planta te cuenta mucho, {n}.' },
    { c: 'planta', t: '¿Esa planta está feliz o solo aguantando, {n}?' },
    { c: 'planta', t: 'A cada planta, su maceta y su luz, {n}.' },
    { c: 'planta', t: '¿Qué planta te preocupa hoy, {n}?' },
    { c: 'planta', t: 'Detrás de cada hoja caída hay una pista, {n}.' },
    { c: 'planta', t: 'Vamos a ver qué necesita tu planta, {n}.' },
    { c: 'planta', t: '¿Le toca riego o le toca descanso, {n}? Averigüémoslo.' },
    { c: 'planta', t: 'Una planta bien entendida crece mejor, {n}.' },
    { c: 'planta', t: '¿Nueva planta en casa, {n}? Cuéntame cuál.' },
    { c: 'planta', t: 'Las plagas se detectan a tiempo si se mira de cerca, {n}.' },
    { c: 'planta', t: 'Dale a tu planta la oportunidad de explicarse, {n}.' },
    { c: 'planta', t: 'Hojas, tallos, raíces: todo cuenta, {n}.' },
    { c: 'planta', t: 'Cuidar bien empieza por reconocer la especie, {n}.' },
    { c: 'planta', t: '¿Esa maceta tiene el tamaño correcto, {n}? Revisémoslo.' },
    { c: 'planta', t: 'Que ninguna planta se quede sin diagnóstico, {n}.' },
    { c: 'planta', t: '¿Le salió algo raro a tu planta, {n}? Mejor mirarlo pronto.' },
    { c: 'planta', t: 'Hora de ponerle cara (y nombre) a tu planta, {n}.' },
    { c: 'planta', t: 'Tu rincón verde merece atención, {n}.' },
    { c: 'planta', t: 'Casi no hay plantas difíciles, solo plantas mal entendidas, {n}.' },
    { c: 'planta', t: 'Cada planta merece una segunda mirada, {n}.' },
    // ── generales · suelo ──
    { c: 'suelo', t: 'Bajo tus pies pasan cosas increíbles, {n}.' },
    { c: 'suelo', t: 'Un buen suelo empieza con una buena mirada, {n}.' },
    { c: 'suelo', t: '¿Cómo está la tierra hoy, {n}?' },
    { c: 'suelo', t: 'Pon un poco de tierra frente a la cámara, {n}.' },
    { c: 'suelo', t: 'Hay suelos que cuentan historias, {n}. ¿Escuchamos el tuyo?' },
    { c: 'suelo', t: 'Rehabilitar un suelo empieza por entenderlo, {n}.' },
    { c: 'suelo', t: 'La tierra tiene memoria, {n}. Vamos a leerla.' },
    { c: 'suelo', t: 'Un puñado de tierra dice más de lo que parece, {n}.' },
    { c: 'suelo', t: 'Un suelo cansado también puede volver a la vida, {n}.' },
    { c: 'suelo', t: '¿Arena, arcilla o limo, {n}? Vamos a descubrirlo.' },
    { c: 'suelo', t: 'El suelo es el primer paso de todo cultivo, {n}.' },
    { c: 'suelo', t: 'Antes de sembrar, conviene escuchar a la tierra, {n}.' },
    { c: 'suelo', t: '¿Y si le damos una revisada a tu terreno, {n}?' },
    { c: 'suelo', t: 'Cada terreno tiene su carácter, {n}.' },
    { c: 'suelo', t: 'Del suelo sale casi todo lo que comemos, {n}.' },
    { c: 'suelo', t: 'Cuidar el suelo es cuidar lo que viene, {n}.' },
    { c: 'suelo', t: 'Una buena base lo cambia todo, {n}.' },
    { c: 'suelo', t: 'Reforestar empieza por conocer el terreno, {n}.' },
    { c: 'suelo', t: 'Sin suelo sano, ningún jardín aguanta, {n}.' },
    { c: 'suelo', t: 'Tu terreno tiene algo que decir, {n}.' },
    { c: 'suelo', t: '¿Un terreno difícil, {n}? Vamos paso a paso.' },
    { c: 'suelo', t: 'Mide, observa, decide, {n}.' },
    { c: 'suelo', t: 'Con buena tierra, lo demás se acomoda, {n}.' },
    { c: 'suelo', t: 'Hoy le toca el turno a tu suelo, {n}.' },
    { c: 'suelo', t: 'Un análisis a tiempo ahorra mucho trabajo después, {n}.' },

    // ── más generales ──
    { c: 'charla', t: 'Aquí empieza tu próximo diagnóstico, {n}.' },
    { c: 'charla', t: '¿Qué descubrimos hoy, {n}?' },
    { c: 'charla', t: 'Te escucho, {n}. ¿Qué pasa en tu jardín?' },
    { c: 'charla', t: 'Siempre hay algo nuevo que observar, {n}.' },
    { c: 'charla', t: 'Tómate tu tiempo, {n}. Yo no me voy.' },
    { c: 'charla', t: 'Un buen cuidador pregunta antes de actuar, {n}.' },
    { c: 'charla', t: 'Mirar con atención ya es cuidar, {n}.' },
    { c: 'charla', t: 'Adelante, {n}. Muéstrame lo que quieras revisar.' },
    { c: 'charla', t: 'Qué alegría verte por aquí, {n}.' },
    { c: 'charla', t: 'Lo importante es empezar, {n}. Lo demás se va viendo.' },
    { c: 'charla', t: '¿Dudas con tus cultivos, {n}? Resolvámoslas.' },
    { c: 'charla', t: 'Hay mucho que aprender de una sola hoja, {n}.' },
    { c: 'charla', t: 'Menos suposiciones y más observación, {n}.' },
    { c: 'charla', t: 'Cada revisión te enseña un poco más, {n}.' },
    { c: 'planta', t: 'Esa planta que te tiene en duda, {n}: vamos a revisarla.' },
    { c: 'planta', t: 'Una hoja, una foto, un diagnóstico, {n}.' },
    { c: 'planta', t: '¿Tu planta tiene sed o tiene frío, {n}? Mejor mirarlo.' },
    { c: 'planta', t: 'Hay plantas que piden ayuda sin hacer ruido, {n}.' },
    { c: 'planta', t: '¿Una planta que no mejora, {n}? Probemos otro ángulo.' },
    { c: 'planta', t: 'Una buena foto vale más que mil sospechas, {n}.' },
    { c: 'planta', t: '¿Esa mancha es nueva, {n}? Vamos a vigilarla.' },
    { c: 'planta', t: 'Cada especie tiene sus gustos, {n}. Averigüemos los de tu planta.' },
    { c: 'planta', t: 'Vamos a darle a tu planta una segunda oportunidad, {n}.' },
    { c: 'planta', t: 'Plantas contentas, casa contenta, {n}.' },
    { c: 'planta', t: 'Todo cuidado empieza con un poco de atención, {n}.' },
    { c: 'suelo', t: 'Lo que no se ve también cuenta: el suelo y las raíces, {n}.' },
    { c: 'suelo', t: 'Conocer tu tierra es la mitad del trabajo, {n}.' },
    { c: 'suelo', t: 'Una buena cosecha empieza bajo tierra, {n}.' },
    { c: 'suelo', t: '¿Y si miramos qué hay debajo, {n}?' },
    { c: 'suelo', t: 'La tierra no miente, {n}. Solo hay que saber leerla.' },
    { c: 'suelo', t: 'Tierra buena, plantas fuertes, {n}.' },
    { c: 'suelo', t: 'Medir hoy, decidir mejor mañana, {n}.' },
    { c: 'suelo', t: 'Un suelo bien conocido rinde más, {n}.' },
    { c: 'suelo', t: 'Cada capa de tu suelo tiene algo que contar, {n}.' },
    { c: 'suelo', t: 'Vamos a ver de qué está hecha tu tierra, {n}.' },

    // ── con saludo según la hora (cuentan como «de la hora») ──
    { c: 'charla', t: '{saludo}, {n}. ¿Qué vamos a revisar?' },
    { c: 'charla', t: '{saludo}. Cuéntame: ¿planta o suelo?' },
    { c: 'charla', t: '{saludo}, {n}. Qué bueno verte.' },
    { c: 'planta', t: '{saludo}, {n}. ¿Cómo va todo por tu jardín?' },
    { c: 'charla', t: '{saludo}. Aquí estoy cuando quieras empezar.' },
    { c: 'charla', t: '{saludo}, {n}. ¿Una planta, un terreno o solo curiosidad?' },
    { c: 'suelo',  t: '{saludo}, {n}. Hoy tengo ganas de hablar de tierra.' },
    { c: 'charla', t: '{saludo}. ¿Qué hay de nuevo por ahí, {n}?' },

    // ── madrugada (0–5 h) ──
    { c: 'charla', when: ['madrugada'], t: '¿Despierto a esta hora, {n}? Las plantas lo aprueban.' },
    { c: 'charla', when: ['madrugada'], t: 'Madrugada tranquila, {n}. Ideal para una revisión sin ruido.' },
    { c: 'charla', when: ['madrugada'], t: 'El mundo duerme, {n}, pero la curiosidad no.' },
    { c: 'charla', when: ['madrugada'], t: 'A esta hora casi todo descansa, {n}. Tú y yo seguimos aquí.' },
    { c: 'charla', when: ['madrugada'], t: '¿Desvelo o madrugón, {n}? Aquí te acompaño.' },
    { c: 'planta', when: ['madrugada'], t: 'Poca luz y muchas ganas de saber, {n}. Adelante.' },
    // ── amanecer · mañana temprano (5–9 h) ──
    { c: 'planta', when: ['amanecer'], t: 'Buen día, {n}. La luz de la mañana es ideal para fotografiar una hoja.' },
    { c: 'suelo',  when: ['amanecer'], t: 'Empieza el día con buena tierra, {n}.' },
    { c: 'charla', when: ['amanecer'], t: 'Primera luz, primera revisión, {n}.' },
    { c: 'planta', when: ['amanecer'], t: 'Café en mano y a mirar el jardín, {n}.' },
    { c: 'planta', when: ['amanecer'], t: 'Qué buen momento para ver cómo amanecieron tus plantas, {n}.' },
    { c: 'charla', when: ['amanecer'], t: 'Las plantas empiezan temprano, {n}. Tú también.' },
    { c: 'suelo',  when: ['amanecer'], t: 'La tierra todavía está fresca, {n}. Buen momento para tocarla.' },
    // ── mañana · media mañana (9–12 h) ──
    { c: 'planta', when: ['mañana'], t: 'Buena mañana para revisar el jardín, {n}.' },
    { c: 'planta', when: ['mañana'], t: 'Media mañana, {n}: buen momento para ver cómo van tus plantas.' },
    { c: 'charla', when: ['mañana'], t: 'Luz limpia y buen ánimo, {n}. Vamos con lo verde.' },
    { c: 'charla', when: ['mañana'], t: 'La mañana rinde, {n}. Un diagnóstico antes del almuerzo.' },
    { c: 'planta', when: ['mañana'], t: 'Hay buena luz, {n}: es buen momento para escanear.' },
    { c: 'suelo',  when: ['mañana'], t: 'Antes de que apriete el sol, {n}, una revisión rápida.' },
    // ── mediodía (12–15 h) ──
    { c: 'charla', when: ['mediodia'], t: 'Mediodía, {n}. Un respiro y un vistazo a lo verde.' },
    { c: 'planta', when: ['mediodia'], t: 'Hora de comer, {n}. Y de preguntarte si tus plantas también tienen sed.' },
    { c: 'planta', when: ['mediodia'], t: 'Sol a plomo, {n}. Ojo con las hojas quemadas.' },
    { c: 'planta', when: ['mediodia'], t: 'Con el sol en lo alto, {n}, revisa que nada se esté resecando.' },
    { c: 'charla', when: ['mediodia'], t: 'Pausa del mediodía, {n}: cinco minutos para lo verde.' },
    { c: 'suelo',  when: ['mediodia'], t: 'A esta hora el suelo se calienta, {n}. Vale la pena tocarlo.' },
    // ── tarde (15–19 h) ──
    { c: 'planta', when: ['tarde'], t: 'Buena tarde para revisar el jardín, {n}.' },
    { c: 'charla', when: ['tarde'], t: 'Un respiro entre pendientes, {n}. Vamos con lo verde.' },
    { c: 'planta', when: ['tarde'], t: 'Ya baja el calor, {n}. Buen momento para pensar en el riego.' },
    { c: 'charla', when: ['tarde'], t: 'Tarde de luz dorada, {n}. Perfecta para mirar el jardín.' },
    { c: 'planta', when: ['tarde'], t: 'Se acerca el atardecer, {n}. ¿Alguna planta que revisar?' },
    { c: 'charla', when: ['tarde'], t: 'Sobremesa verde, {n}: un diagnóstico y a seguir.' },
    // ── anochecer (19–21 h) ──
    { c: 'charla', when: ['anochecer'], t: 'Atardece, {n}. Última revisión del día.' },
    { c: 'planta', when: ['anochecer'], t: 'Cae la noche, {n}. Si vas a escanear, busca buena luz.' },
    { c: 'planta', when: ['anochecer'], t: 'Cena y plantas, {n}: ¿cómo les fue hoy?' },
    { c: 'charla', when: ['anochecer'], t: 'Fin de jornada, {n}. Un vistazo a lo verde antes de descansar.' },
    { c: 'planta', when: ['anochecer'], t: 'Buen momento para planear el riego de mañana, {n}.' },
    { c: 'charla', when: ['anochecer'], t: 'El día se despide, {n}. ¿Revisamos algo antes de cerrar?' },
    // ── noche (21–24 h) ──
    { c: 'planta', when: ['noche'], t: 'Noche tranquila, {n}. Ideal para planear el riego de mañana.' },
    { c: 'charla', when: ['noche'], t: 'Todavía hay tiempo para una última revisión, {n}.' },
    { c: 'charla', when: ['noche'], t: 'Todo está en calma, {n}. Revisemos sin prisa.' },
    { c: 'charla', when: ['noche'], t: 'Antes de dormir, {n}, un último diagnóstico.' },
    { c: 'suelo',  when: ['noche'], t: 'La noche es buena para ordenar las ideas del terreno, {n}.' },
    { c: 'charla', when: ['noche'], t: 'Descansa pronto, {n}. Mañana la luz hará el resto.' },
    // ── más según la hora ──
    { c: 'charla', when: ['madrugada'], t: 'Qué buena hora para pensar con calma, {n}.' },
    { c: 'charla', when: ['madrugada'], t: 'Hasta las plantas descansan ahora, {n}. Tú pregúntame lo que quieras.' },
    { c: 'charla', when: ['madrugada'], t: '¿Noche larga o día temprano, {n}? Aquí estoy.' },
    { c: 'planta', when: ['amanecer'], t: 'Empieza el día con el jardín, {n}.' },
    { c: 'planta', when: ['amanecer'], t: 'Amanece, {n}. ¿Cómo estarán tus plantas?' },
    { c: 'charla', when: ['amanecer'], t: 'Qué gusto verte tan temprano, {n}.' },
    { c: 'charla', when: ['mañana'], t: 'Mañana con ganas, {n}. ¿Qué revisamos?' },
    { c: 'planta', when: ['mañana'], t: 'Aprovecha la mañana, {n}: hay buena luz para escanear.' },
    { c: 'planta', when: ['mañana'], t: 'La mañana avanza, {n}. Un vistazo rápido al jardín.' },
    { c: 'charla', when: ['mediodia'], t: 'Se acerca la hora de comer, {n}. Antes, un vistazo a lo verde.' },
    { c: 'planta', when: ['mediodia'], t: 'Pleno día, {n}. ¿Cómo aguantan tus plantas el sol?' },
    { c: 'charla', when: ['mediodia'], t: 'Un descanso al mediodía, {n}. ¿Qué toca revisar?' },
    { c: 'charla', when: ['tarde'], t: 'La tarde invita a mirar el jardín, {n}.' },
    { c: 'planta', when: ['tarde'], t: 'Tarde tranquila, {n}. ¿Revisamos esa planta que te inquieta?' },
    { c: 'planta', when: ['tarde'], t: 'Ya pasó lo más fuerte del sol, {n}. Buen rato para revisar.' },
    { c: 'charla', when: ['anochecer'], t: 'Se va la luz, {n}. Un último vistazo al jardín.' },
    { c: 'planta', when: ['anochecer'], t: 'Anochece, {n}. ¿Cómo les fue a tus plantas hoy?' },
    { c: 'charla', when: ['anochecer'], t: 'Hora de cerrar el día con algo verde, {n}.' },
    { c: 'charla', when: ['noche'], t: 'Buenas noches también para tus plantas, {n}.' },
    { c: 'charla', when: ['noche'], t: 'Noche de pensar en el jardín, {n}.' },
    { c: 'charla', when: ['noche'], t: 'Si te quedó una duda del día, {n}, aquí la resolvemos.' },
];

const GREET_TIPS = [
    // ── generales · plantas ──
    { c: 'planta', t: 'Antes de regar, mete el dedo 2 o 3 cm en la tierra: si sigue húmeda, espera.' },
    { c: 'planta', t: 'Muchas plantas de interior sufren más por exceso de riego que por falta.' },
    { c: 'planta', t: 'Revisa el envés de las hojas: ahí se esconden muchas plagas.' },
    { c: 'planta', t: 'Una hoja amarilla no siempre pide agua; a veces pide menos.' },
    { c: 'planta', t: 'Una maceta sin orificios de drenaje es una de las causas más comunes de raíces podridas.' },
    { c: 'planta', t: 'Girar la maceta un cuarto de vuelta cada semana ayuda a que la planta crezca pareja.' },
    { c: 'planta', t: 'Para escanear una planta, luz natural y una foto de cerca de la hoja hacen toda la diferencia.' },
    { c: 'planta', t: 'En invierno, casi todas las plantas de interior piden menos riego y menos abono.' },
    { c: 'planta', t: 'Las puntas secas suelen hablar de aire seco, exceso de sales o riego irregular.' },
    { c: 'planta', t: 'Si una planta se estira buscando la ventana, probablemente le falta luz.' },
    { c: 'planta', t: 'Hojas pálidas y tallos largos y flacos son pistas clásicas de poca luz.' },
    { c: 'planta', t: 'Limpiar el polvo de las hojas con un paño húmedo les ayuda a aprovechar mejor la luz.' },
    { c: 'planta', t: 'Un platito con agua estancada bajo la maceta puede ahogar las raíces: vacíalo después de regar.' },
    { c: 'planta', t: 'Las plantas nuevas necesitan unos días para adaptarse: no las cambies de lugar a cada rato.' },
    { c: 'planta', t: 'Trasplanta cuando las raíces ya salgan por los orificios de drenaje; antes, mejor no.' },
    { c: 'planta', t: 'Una maceta mucho más grande de lo necesario retiene agua de más y puede pudrir las raíces.' },
    { c: 'planta', t: 'Las manchas con halo amarillo pueden indicar hongos o bacterias: aísla la planta si puedes.' },
    { c: 'planta', t: 'Un polvillo blanco sobre las hojas suele ser oídio, un hongo que se esparce rápido.' },
    { c: 'planta', t: 'Si ves telarañas finas entre las hojas, revisa si hay ácaros: les encanta el aire seco.' },
    { c: 'planta', t: 'Una sustancia pegajosa en las hojas suele venir de pulgones o cochinillas.' },
    { c: 'planta', t: 'Regar poco y seguido moja solo la superficie; regar a fondo y espaciar fortalece las raíces.' },
    { c: 'planta', t: 'Las plantas de sombra también necesitan algo de luz: sombra no es oscuridad.' },
    { c: 'planta', t: 'Un cambio brusco de luz o de lugar puede hacer que una planta tire hojas.' },
    { c: 'planta', t: 'Antes de abonar, asegúrate de que la planta esté sana: abonar una planta enferma no la cura.' },
    { c: 'planta', t: 'Más abono no es mejor: el exceso quema raíces y deja sales en la tierra.' },
    { c: 'planta', t: 'Para una buena foto de planta: fondo liso, hoja completa y nada de flash directo.' },
    { c: 'planta', t: 'Si la base del tallo está blanda y oscura, puede ser pudrición: revisa el riego y el drenaje.' },
    { c: 'planta', t: 'Las suculentas y los cactus prefieren secarse del todo entre riegos.' },
    { c: 'planta', t: 'Las hojas enrolladas pueden venir de sed, calor excesivo o plagas: observa cuál encaja.' },
    { c: 'planta', t: 'Rociar las hojas no sustituye al riego: el agua que necesitan las raíces viene de la tierra.' },
    { c: 'planta', t: 'Un tutor a tiempo evita tallos quebrados en las plantas trepadoras.' },
    { c: 'planta', t: 'Retira las hojas secas o dañadas: la planta gasta menos energía y hay menos riesgo de hongos.' },
    { c: 'planta', t: 'Fotografía tu planta cada mes: comparar fotos es la mejor forma de ver si mejora.' },
    { c: 'planta', t: 'El agua a temperatura ambiente es más amable con las plantas tropicales que la muy fría.' },
    // ── generales · suelo ──
    { c: 'suelo', t: 'Un puñado de tierra sana alberga más organismos vivos que personas hay en el planeta.' },
    { c: 'suelo', t: 'Las lombrices airean el suelo y lo enriquecen mientras trabajan: son grandes aliadas.' },
    { c: 'suelo', t: 'Un suelo cubierto con mantillo pierde menos agua y se erosiona menos que uno desnudo.' },
    { c: 'suelo', t: 'El pH decide qué nutrientes puede absorber una planta, aunque el suelo los tenga.' },
    { c: 'suelo', t: 'Formar unos pocos centímetros de suelo puede tardar cientos de años. Vale la pena cuidarlo.' },
    { c: 'suelo', t: 'Con la prueba del frasco puedes estimar cuánta arena, limo y arcilla tiene tu tierra, sin instrumentos.' },
    { c: 'suelo', t: 'Si tu suelo forma una costra blanca al secarse, puede haber exceso de sales.' },
    { c: 'suelo', t: 'Un suelo compactado deja pasar menos agua y menos raíces: airearlo ayuda.' },
    { c: 'suelo', t: 'Si la tierra seca hace espuma con vinagre, probablemente tiene carbonatos y su pH es alcalino.' },
    { c: 'suelo', t: 'La materia orgánica es el «banco» del suelo: guarda agua y nutrientes para cuando las plantas los necesiten.' },
    { c: 'suelo', t: 'Un suelo arenoso drena rápido pero retiene poco; añadir compost le ayuda a guardar agua.' },
    { c: 'suelo', t: 'Un suelo arcilloso retiene agua y nutrientes, pero se encharca: mejora con materia orgánica y buen drenaje.' },
    { c: 'suelo', t: 'Los suelos francos, con arena, limo y arcilla en equilibrio, son los más fáciles de cultivar.' },
    { c: 'suelo', t: 'Dejar el suelo desnudo lo expone a la lluvia y al sol: una cubierta vegetal lo protege.' },
    { c: 'suelo', t: 'Pisar siempre el mismo lugar compacta el suelo; marca senderos fijos y deja el resto en paz.' },
    { c: 'suelo', t: 'Las raíces también necesitan aire: un suelo sin poros las asfixia.' },
    { c: 'suelo', t: 'Tras un incendio el suelo queda expuesto: cubrirlo y frenar el agua ayuda a evitar la erosión.' },
    { c: 'suelo', t: 'El compost casero convierte restos de cocina y jardín en alimento para el suelo.' },
    { c: 'suelo', t: 'Rotar cultivos ayuda a que el suelo no se agote siempre de los mismos nutrientes.' },
    { c: 'suelo', t: 'Ese olor a tierra mojada suele indicar vida microbiana activa.' },
    { c: 'suelo', t: 'Un suelo muy ácido bloquea nutrientes y uno muy alcalino también: la clave es el equilibrio.' },
    { c: 'suelo', t: 'La cal sube el pH de los suelos ácidos; el azufre o la materia orgánica ayudan a bajarlo en los alcalinos.' },
    { c: 'suelo', t: 'La salinidad afecta a las plantas aunque no se vea: una medición vale más que una impresión.' },
    { c: 'suelo', t: 'Las raíces de árboles y arbustos sujetan el suelo y frenan la erosión en las pendientes.' },
    { c: 'suelo', t: 'Un suelo sano retiene la lluvia como una esponja y reduce los encharcamientos.' },
    { c: 'suelo', t: 'Apretar la tierra entre los dedos ya da pistas: ¿se desmorona o forma una bolita?' },
    { c: 'suelo', t: 'Una muestra de 0 a 20 cm cubre la zona donde trabajan la mayoría de las raíces de jardín y cultivo.' },
    { c: 'suelo', t: 'Para una muestra representativa, mezcla tierra de varios puntos del terreno.' },
    { c: 'suelo', t: 'Después de llover, el suelo muestra su drenaje: si el agua tarda en irse, hay que mejorarlo.' },
    { c: 'suelo', t: 'Los hongos del suelo tejen redes que conectan raíces y ayudan a compartir agua y nutrientes.' },
    { c: 'suelo', t: 'Los restos de cosecha sobre el suelo se convierten con el tiempo en materia orgánica.' },
    // ── generales · charla ──
    { c: 'charla', t: 'Escanea con la cámara o llena un formulario: tú eliges cómo empezar.' },
    { c: 'charla', t: 'Cada diagnóstico empieza con una buena pregunta. ¿Cuál es la tuya?' },
    { c: 'charla', t: 'Si no sabes por dónde empezar, una foto suele ser suficiente.' },
    { c: 'charla', t: 'Tu jardín no tiene que ser perfecto para pedir ayuda.' },
    { c: 'charla', t: 'A todos se nos ha pasado la mano con el riego alguna vez.' },
    { c: 'charla', t: 'Aquí no hay preguntas tontas sobre plantas. Tómate tu tiempo.' },
    { c: 'charla', t: 'Puedes preguntarle a EcoChat lo que quieras sobre tu informe.' },
    { c: 'charla', t: 'Con el informe en PDF guardas el diagnóstico y lo revisas cuando quieras.' },
    { c: 'charla', t: 'Puedes agendar recordatorios de cuidado para no olvidar el riego.' },
    { c: 'charla', t: 'Activar tu ubicación suma datos satelitales de suelo y plantas de tu región. Solo si tú quieres.' },
    { c: 'charla', t: 'Mientras más mediciones reales aportes, más precisa es la IA. Lo que no midas se marca como «sin medición».' },
    { c: 'charla', t: 'Equivocarse con una planta es parte de aprender a cuidarlas.' },
    { c: 'charla', t: 'Ir poco a poco funciona: cambia una cosa a la vez y observa qué pasa.' },
    { c: 'charla', t: 'Anota cuándo riegas y qué cambias: tus notas ayudan a entender los resultados.' },
    { c: 'charla', t: 'Compartir tus mediciones de forma anónima es opcional y lo decides tú.' },
    { c: 'charla', t: 'La IA orienta, pero tú conoces tu jardín mejor que nadie.' },
    { c: 'charla', t: 'Un diagnóstico es un punto de partida: observa y ajusta con el tiempo.' },
    { c: 'charla', t: 'Si algo no cuadra, vuelve a escanear con otra foto y más luz.' },
    { c: 'charla', t: 'Cuidar plantas es paciencia con buenos datos.' },
    { c: 'charla', t: 'Lo que hoy parece un problema, mañana es una buena historia de jardín.' },
    { c: 'charla', t: 'Escanear toma segundos; entender lo que ves es un gusto que dura meses.' },
    { c: 'charla', t: 'Tu cámara y un poco de luz natural son todo lo que necesitas para empezar.' },

    // ── más generales ──
    { c: 'planta', t: 'Si la tierra se separa del borde de la maceta, está muy seca: riega despacio para que vuelva a absorber.' },
    { c: 'planta', t: 'Los esquejes enraízan mejor con luz brillante pero sin sol directo.' },
    { c: 'planta', t: 'Hojas con bordes marrones y crujientes suelen indicar aire seco, sol directo o riego irregular.' },
    { c: 'planta', t: 'Un abono equilibrado y en poca cantidad es mejor que una dosis fuerte de golpe.' },
    { c: 'planta', t: 'Las plantas también descansan en invierno: si crecen poco, no siempre es mala señal.' },
    { c: 'planta', t: 'Una plaga detectada a tiempo se controla con mucho menos esfuerzo: revisa tus plantas con regularidad.' },
    { c: 'planta', t: 'Si compras una planta nueva, mantenla unos días aparte de las demás antes de juntarlas.' },
    { c: 'planta', t: 'Una costra blanca sobre la tierra de la maceta suele ser sal acumulada: un riego a fondo de vez en cuando la arrastra.' },
    { c: 'planta', t: 'Las hojas que caen de golpe suelen avisar de un cambio brusco de luz, de temperatura o de riego.' },
    { c: 'planta', t: 'Las raíces sanas suelen verse claras y firmes; las oscuras y blandas piden atención.' },
    { c: 'planta', t: 'Podar a tiempo ayuda a que la planta se ramifique y crezca más tupida.' },
    { c: 'planta', t: 'Cerca de un aire acondicionado o una calefacción, las plantas sufren por el aire seco y las corrientes.' },
    { c: 'planta', t: 'Para saber si necesita agua, levanta la maceta: pesa mucho menos cuando la tierra está seca.' },
    { c: 'planta', t: 'Si los botones de las flores se caen antes de abrir, puede ser por cambios de luz, de riego o de temperatura.' },
    { c: 'planta', t: 'Agrupar plantas sube un poco la humedad a su alrededor y ayuda a las que prefieren ambiente húmedo.' },
    { c: 'planta', t: 'Con una planta de hojas manchadas, observa unos días: ¿las manchas crecen o se quedan igual?' },
    { c: 'planta', t: 'Las mariquitas comen pulgones: antes de fumigar, mira quién más vive en tu planta.' },
    { c: 'planta', t: 'Si vas a abonar, hazlo con la tierra ya húmeda para no quemar las raíces.' },
    { c: 'suelo', t: 'La textura del suelo casi no cambia, pero su estructura sí mejora con compost y buen manejo.' },
    { c: 'suelo', t: 'Un suelo de color oscuro suele tener más materia orgánica, aunque no siempre.' },
    { c: 'suelo', t: 'La humedad se mide mejor a la profundidad de las raíces que solo en la superficie.' },
    { c: 'suelo', t: 'Los restos de poda triturados sirven de mantillo y alimentan el suelo poco a poco.' },
    { c: 'suelo', t: 'Las leguminosas, como los frijoles, ayudan a aportar nitrógeno al suelo.' },
    { c: 'suelo', t: 'Labrar en exceso rompe la estructura del suelo y acelera la pérdida de materia orgánica.' },
    { c: 'suelo', t: 'Cuando el suelo se encharca, las raíces se quedan sin oxígeno: el drenaje importa tanto como el riego.' },
    { c: 'suelo', t: 'La conductividad eléctrica da una idea de cuántas sales hay disueltas en el suelo.' },
    { c: 'suelo', t: 'Un pH entre 6 y 7 es cómodo para la mayoría de los cultivos de huerta.' },
    { c: 'suelo', t: 'El nitrógeno, el fósforo y el potasio son los nutrientes que las plantas necesitan en mayor cantidad.' },
    { c: 'suelo', t: 'El agua de lluvia se infiltra mejor en un suelo con cobertura vegetal que en uno desnudo.' },
    { c: 'suelo', t: 'Las terrazas y las barreras de piedra frenan el agua en las pendientes y reducen la erosión.' },
    { c: 'suelo', t: 'Las manchas de color en un terreno suelen marcar zonas con más o menos humedad o materia orgánica.' },
    { c: 'suelo', t: 'Los suelos de zonas áridas suelen ser más alcalinos; los de zonas muy lluviosas, más ácidos.' },
    { c: 'suelo', t: 'Cubrir el suelo con paja o corteza reduce las malezas y conserva la humedad.' },
    { c: 'suelo', t: 'Un buen compost huele a bosque, no a podrido: si huele mal, le falta aire o le sobra agua.' },
    { c: 'charla', t: 'Una foto nítida y bien iluminada se interpreta mucho mejor que una borrosa.' },
    { c: 'charla', t: 'Si tienes varias plantas, escanéalas una por una: cada diagnóstico queda más claro.' },
    { c: 'charla', t: 'Escanear la misma planta cada cierto tiempo te ayuda a ver cómo evoluciona.' },
    { c: 'charla', t: 'La paciencia también es un nutriente: muchas plantas tardan semanas en reaccionar a un cambio.' },
    { c: 'charla', t: 'Tu informe no sustituye a un laboratorio, pero sí te da buenas pistas para decidir.' },
    { c: 'charla', t: 'Si algo se ve muy grave, consulta también con un vivero o un técnico agrícola de tu zona.' },
    { c: 'charla', t: 'Cada jardín es distinto: lo que funciona en el de un vecino puede no funcionar en el tuyo.' },
    { c: 'charla', t: 'Los datos de satélite dan contexto de tu zona, pero tu observación en terreno vale mucho.' },
    { c: 'charla', t: 'Plantar en la temporada correcta ahorra muchos problemas: averigua qué conviene sembrar ahora en tu zona.' },
    { c: 'charla', t: 'Que una planta sobreviva no significa que esté bien: mira también su color, su brillo y su crecimiento.' },

    // ── según la hora (solo salen de vez en cuando) ──
    { c: 'charla', when: ['madrugada'], t: 'De madrugada nada urge: aprovecha para anotar qué plantas quieres revisar mañana.' },
    { c: 'planta', when: ['madrugada'], t: 'Revisar fotos viejas de tus plantas es una forma tranquila de ver cuánto han mejorado.' },
    { c: 'charla', when: ['madrugada'], t: 'La luz artificial cambia los colores: para escanear mejor, espera a la luz del día.' },
    { c: 'planta', when: ['amanecer'], t: 'Regar temprano deja que el agua entre al suelo antes de que el sol la evapore.' },
    { c: 'planta', when: ['amanecer'], t: 'Con la luz suave de la mañana, las fotos de hojas salen sin sombras duras.' },
    { c: 'planta', when: ['amanecer'], t: 'Por la mañana las hojas muestran mejor su estado: aún no han sufrido el calor del día.' },
    { c: 'planta', when: ['amanecer'], t: 'El rocío sobre las hojas puede ayudarte a ver telarañas finas de ácaros.' },
    { c: 'planta', when: ['mañana'],    t: 'A media mañana la luz suele ser buena para fotografiar hojas sin sombras fuertes.' },
    { c: 'planta', when: ['mañana'],    t: 'Si vas a trasplantar, hazlo temprano o al caer la tarde, no con el sol fuerte.' },
    { c: 'suelo',  when: ['mañana'],    t: 'Es buen momento para revisar la humedad de la tierra antes de que suba el calor.' },
    { c: 'planta', when: ['mediodia'],  t: 'Evita regar a pleno sol del mediodía: parte del agua se evapora antes de llegar a las raíces.' },
    { c: 'planta', when: ['mediodia'],  t: 'Si una planta se ve caída al mediodía y se recupera por la tarde, puede ser solo calor y no falta de agua.' },
    { c: 'charla', when: ['mediodia'],  t: 'La luz del mediodía da sombras duras: para escanear una hoja, busca sombra abierta.' },
    { c: 'planta', when: ['tarde'],     t: 'Cuando baja el calor es buen momento de regar: el agua rinde más.' },
    { c: 'charla', when: ['tarde'],     t: 'La luz de la tarde es cálida y suave, pero puede cambiar el color de las hojas en la foto.' },
    { c: 'suelo',  when: ['tarde'],     t: 'A media tarde puedes tocar la tierra y comprobar si ya se secó lo suficiente para regar.' },
    { c: 'planta', when: ['anochecer'], t: 'Si riegas al atardecer, moja la tierra y no las hojas: las hojas húmedas toda la noche favorecen hongos.' },
    { c: 'charla', when: ['anochecer'], t: 'Con poca luz, las fotos salen borrosas y con colores falsos: deja el escaneo para mañana si puedes.' },
    { c: 'charla', when: ['anochecer'], t: 'Anota qué plantas necesitan revisión mañana: tu yo de la mañana te lo agradecerá.' },
    { c: 'charla', when: ['noche'],     t: 'De noche, bajo luz artificial, el color de las hojas se ve distinto: confirma con luz natural.' },
    { c: 'planta', when: ['noche'],     t: 'Evita mojar las hojas de noche: la humedad prolongada favorece los hongos.' },
    { c: 'charla', when: ['noche'],     t: 'Dormir bien también es cuidar el jardín: mañana lo verás con ojos nuevos.' },
    // ── más según la hora ──
    { c: 'planta', when: ['madrugada'], t: 'A estas horas todo está en calma: buen momento para planear el riego del día.' },
    { c: 'charla', when: ['madrugada'], t: 'Aprovecha para repasar tus notas de riego y abonado de la semana.' },
    { c: 'charla', when: ['madrugada'], t: 'Si piensas escanear mañana, deja la planta en un lugar con buena luz natural.' },
    { c: 'planta', when: ['madrugada'], t: 'Dormir también es parte del cuidado: tus plantas aguantan hasta mañana.' },
    { c: 'suelo', when: ['amanecer'], t: 'Por la mañana el suelo está más fresco: es buen momento para sentir si está húmedo o seco.' },
    { c: 'planta', when: ['amanecer'], t: 'Regar temprano da tiempo a que las hojas se sequen durante el día.' },
    { c: 'planta', when: ['amanecer'], t: 'Si tu planta amanece con gotitas en las puntas, suele ser gutación: es normal en muchas especies.' },
    { c: 'planta', when: ['amanecer'], t: 'Las primeras horas de luz son buenas para ver si hay plagas activas en las hojas.' },
    { c: 'planta', when: ['mañana'], t: 'Con la luz de media mañana se ve bien el verdadero color de las hojas.' },
    { c: 'planta', when: ['mañana'], t: 'Es buen momento para podar o retirar hojas secas antes de que apriete el calor.' },
    { c: 'suelo', when: ['mañana'], t: 'Si vas a medir el suelo, hazlo antes de que el sol seque la superficie.' },
    { c: 'planta', when: ['mañana'], t: 'Por la mañana puedes mover las macetas a un sol suave y evitar el más fuerte de la tarde.' },
    { c: 'planta', when: ['mediodia'], t: 'Las macetas oscuras se calientan mucho con el sol del mediodía y pueden estresar las raíces.' },
    { c: 'planta', when: ['mediodia'], t: 'Si tu terraza recibe sol de mediodía, una malla de sombra puede salvar a las plantas sensibles.' },
    { c: 'suelo', when: ['mediodia'], t: 'Toca la tierra a 2 cm de profundidad: si está seca, riega al caer la tarde.' },
    { c: 'planta', when: ['mediodia'], t: 'Las hojas quemadas por el sol muestran manchas secas y pálidas del lado que mira al sol.' },
    { c: 'planta', when: ['tarde'], t: 'Por la tarde puedes comprobar si el riego de la mañana alcanzó o la tierra ya está seca.' },
    { c: 'planta', when: ['tarde'], t: 'La tarde es buena hora para trasplantar: la planta descansa de noche y sufre menos el cambio.' },
    { c: 'planta', when: ['tarde'], t: 'Mira cómo da la luz de la tarde en tus plantas: te dice si el lugar es el adecuado.' },
    { c: 'planta', when: ['anochecer'], t: 'Antes de que oscurezca, vacía los platitos de las macetas para que no queden con agua toda la noche.' },
    { c: 'charla', when: ['anochecer'], t: 'Es buen rato para anotar qué hiciste hoy con cada planta.' },
    { c: 'planta', when: ['anochecer'], t: 'Con la luz que queda, mira las hojas a contraluz: se ven mejor las telarañas y los insectos pequeños.' },
    { c: 'planta', when: ['anochecer'], t: 'Si hay riesgo de helada esta noche, protege tus plantas sensibles antes de que baje la temperatura.' },
    { c: 'planta', when: ['noche'], t: 'Algunas plantas necesitan sus horas de oscuridad: evita dejarles la luz encendida toda la noche.' },
    { c: 'charla', when: ['noche'], t: 'Deja anotado para mañana lo que quieras revisar: riego, abono o una foto con luz natural.' },
    { c: 'charla', when: ['noche'], t: 'La noche es buen momento para leer con calma el informe de tu planta y decidir qué cambiar.' },
    { c: 'planta', when: ['noche'], t: 'Si en tu zona refresca de noche, las plantas sensibles al frío agradecen un lugar más abrigado.' },
];

// Franja del día para una hora (0–23)
function greetPeriod(hour) {
    const p = GREET_PERIODS.find((x) => hour >= x.from && hour < x.to);
    return p ? p.id : 'noche';
}
function greetHello(hour) {
    return hour >= 5 && hour < 12 ? 'Buenos días' : hour >= 12 && hour < 19 ? 'Buenas tardes' : 'Buenas noches';
}
// Rellena {saludo} y {n}. Sin nombre se quita {n} junto con su coma. Se usan funciones de reemplazo para que un nombre con «$&» o similares no altere la frase.
function greetFill(t, hour, first) {
    let out = t.replace(/\{saludo\}/g, () => greetHello(hour));
    out = first ? out.replace(/\{n\}/g, () => first) : out.replace(/,\s*\{n\}/g, '').replace(/\s*\{n\}/g, '');
    return out.charAt(0).toUpperCase() + out.slice(1);
}
// «De la hora»: tiene franjas propias o lleva el saludo según la hora
const greetIsTimed = (x) => !!x.when || x.t.indexOf('{saludo}') !== -1;
const greetFitsNow = (x, part) => !x.when || x.when.indexOf(part) !== -1;
const greetPickOne = (arr, rnd) => arr[Math.floor(rnd() * arr.length)];

// Titular: con probabilidad GREET_TIME_CHANCE sale uno «de la hora»; si no, uno general.
// Nunca repite lo que está en `exclude` (historial reciente); si un grupo se agota, usa el otro.
//   allowTimed   false para forzar uno general (se usa tras dos visitas seguidas con titular «de la hora»)
function greetDrawHeadline(items, part, exclude, opts = {}) {
    const { allowTimed = true, rnd = Math.random, chance = GREET_TIME_CHANCE } = opts;
    const fresh = (x) => exclude.indexOf(x.t) === -1;
    const timed = items.filter((x) => greetIsTimed(x) && greetFitsNow(x, part));
    const plain = items.filter((x) => !greetIsTimed(x));
    const wantTimed = allowTimed && rnd() < chance;
    const first = wantTimed ? timed : plain, second = wantTimed ? plain : (allowTimed ? timed : []);
    let pool = first.filter(fresh);
    if (!pool.length) pool = second.filter(fresh);
    if (!pool.length) pool = first.length ? first : (second.length ? second : items);
    return greetPickOne(pool, rnd);
}
// Línea de apoyo: igual, con GREET_TIP_TIME_CHANCE.
//   theme        tema preferido (el del titular); null = cualquiera
//   allowTimed   false si la frase anterior ya fue «de la hora»
function greetDrawTip(items, part, exclude, opts = {}) {
    const { theme = null, allowTimed = true, rnd = Math.random, chance = GREET_TIP_TIME_CHANCE } = opts;
    const fresh = (x) => exclude.indexOf(x.t) === -1;
    const timed = allowTimed ? items.filter((x) => x.when && greetFitsNow(x, part) && fresh(x)) : [];
    const plain = items.filter((x) => !x.when && fresh(x));
    let pool = timed.length && rnd() < chance ? timed : (plain.length ? plain : timed);
    if (!pool.length) pool = items.filter((x) => !x.when);
    if (theme && theme !== 'charla') {
        const same = pool.filter((x) => x.c === theme);
        if (same.length) pool = same;
    }
    return greetPickOne(pool, rnd);
}

class EcoScanApp {
    constructor() {
        this.currentLang    = 'es';
        this.currentResults = null;
        this.currentData    = null;
        this.videoStream    = null;
        this.scanResult     = null;
        this.userRegion     = '';
        this.userLat        = '';
        this.userLng        = '';
        this.BASE_URL       = API_BASE;
        this.init();
    }

    init() {
        this.bindEvents();
        this.initBackground();
        this.initGreeting();
        this.initPHSlider();
        this.initInstallPrompt();
        this.initTutorial();
        window.addEventListener('offline', () => this.showToast('Sin conexión a internet: las funciones de IA no estarán disponibles hasta que vuelva.', 'warning'));
    }

    // ── BIND EVENTS ──────────────────────────────────────────────────────────
    bindEvents() {
        document.getElementById('logoutBtn')?.addEventListener('click', async () => {
            try { await fetch(this.BASE_URL + '/api/logout', { method:'POST', credentials:'include' }); } catch(e) {}
            window.location.href = 'login.html';
        });
        document.getElementById('soilForm')?.addEventListener('submit', (e) => this.handleSubmit(e));
        document.getElementById('scanSoilCard')?.addEventListener('click', () => this.openScanner('soil'));
        document.getElementById('scanPlantCard')?.addEventListener('click', () => this.openScanner('plant'));
        document.getElementById('toggleManualBtn')?.addEventListener('click', () => this.toggleManualForm('soil'));
        document.getElementById('togglePlantFormBtn')?.addEventListener('click', () => this.toggleManualForm('plant'));
        document.getElementById('plantForm')?.addEventListener('submit', (e) => this.handlePlantForm(e));
        document.getElementById('closeResults')?.addEventListener('click', () => this.hideResults());
        document.getElementById('downloadPdfBtn')?.addEventListener('click', () => this.downloadPDF());
        const locBox = document.getElementById('enableLocation');
        if (locBox) { locBox.checked = false; locBox.addEventListener('change', (e) => this.toggleLocation(e.target.checked)); }
        document.getElementById('showGuidesBtn')?.addEventListener('click', () => this.showGuidesModal());
        document.getElementById('scheduleReminder')?.addEventListener('click', () => this.scheduleReminder());
        document.getElementById('enableAlerts')?.addEventListener('change', (e) => {
            document.getElementById('alertFrequency')?.classList.toggle('hidden', !e.target.checked);
        });
        document.getElementById('closeGuidesModal')?.addEventListener('click', () => this.closeGuidesModal());
        document.querySelectorAll('.guide-tab').forEach(tab =>
            tab.addEventListener('click', (e) => this.switchGuideTab(e.target.dataset.guide)));
        document.getElementById('closeCameraModal')?.addEventListener('click', () => this.closeCameraModal());
        document.getElementById('capturePhoto')?.addEventListener('click', () => this.capturePhoto());
        document.getElementById('uploadPhoto')?.addEventListener('click', () => document.getElementById('photoUpload').click());
        document.getElementById('photoUpload')?.addEventListener('change', (e) => this.handleImageUpload(e));
        document.getElementById('applyScanResult')?.addEventListener('click', () => this.applyScanToForm());
        document.getElementById('closeTutorial')?.addEventListener('click', () => this.closeTutorial());
        document.getElementById('skipTutorial')?.addEventListener('click', () => this.closeTutorial());
        document.getElementById('prevStep')?.addEventListener('click', () => this.prevTutorialStep());
        document.getElementById('nextStep')?.addEventListener('click', () => this.nextTutorialStep());
    }

    // ── PWA ───────────────────────────────────────────────────────────────────
    initInstallPrompt() {
        let dp;
        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault(); dp = e;
            const btn = document.getElementById('installBtn');
            if (btn) { btn.style.display='flex'; btn.onclick = async () => { if (dp) { dp.prompt(); await dp.userChoice; dp=null; } }; }
        });
    }

    // ── TUTORIAL ──────────────────────────────────────────────────────────────
    initTutorial() {
        if (!localStorage.getItem('tutorialShown')) setTimeout(() => this.showTutorial(), 700);
    }

    showTutorial() {
        this.currentTutorialStep = 0;
        this.tutorialSteps = [
            { title:'🌱 Bienvenido a EcoScan', desc:'Escanea una planta o un suelo con tu cámara y la IA te entrega un diagnóstico claro en segundos.', icon:'fas fa-seedling' },
            { title:'🌿 Plantas: nombre, plagas y cuidados', desc:'Identifica la especie, detecta manchas y enfermedades y recibe los cuidados y la maceta ideal para ella.', icon:'fas fa-leaf' },
            { title:'📍 Suelo con datos satelitales', desc:'Activa tu ubicación para sumar datos satelitales de tu suelo y recibir plantas adecuadas para tu región.', icon:'fas fa-map-marker-alt' },
            { title:'💬 Formularios y EcoChat', desc:'¿Tienes mediciones? Usa los formularios. Y pregúntale a EcoChat lo que quieras sobre tu informe.', icon:'fas fa-comments' }
        ];
        this.updateTutorialStep();
        document.getElementById('tutorialModal').style.display = 'flex';
    }

    updateTutorialStep() {
        const step = this.tutorialSteps[this.currentTutorialStep];
        const stepDiv = document.getElementById('tutorialStep');
        const counter = document.getElementById('stepCounter');
        const prevBtn = document.getElementById('prevStep');
        const nextBtn = document.getElementById('nextStep');
        if (stepDiv) stepDiv.innerHTML = `<div style="text-align:center;padding:1rem 0;"><i class="${step.icon}" style="font-size:3rem;color:var(--accent-primary);margin-bottom:1rem;display:block;"></i><h4 style="margin-bottom:.75rem;">${step.title}</h4><p style="color:var(--text-secondary);line-height:1.6;">${step.desc}</p></div>`;
        if (counter) counter.textContent = `Paso ${this.currentTutorialStep + 1}/${this.tutorialSteps.length}`;
        if (prevBtn) prevBtn.disabled = this.currentTutorialStep === 0;
        if (nextBtn) nextBtn.textContent = this.currentTutorialStep === this.tutorialSteps.length - 1 ? 'Comenzar' : 'Siguiente';
    }

    nextTutorialStep() {
        if (this.currentTutorialStep < this.tutorialSteps.length - 1) { this.currentTutorialStep++; this.updateTutorialStep(); }
        else this.closeTutorial();
    }

    prevTutorialStep() {
        if (this.currentTutorialStep > 0) { this.currentTutorialStep--; this.updateTutorialStep(); }
    }

    closeTutorial() {
        document.getElementById('tutorialModal').style.display = 'none';
        localStorage.setItem('tutorialShown', 'true');
    }

    // ── PH SLIDER ────────────────────────────────────────────────────────────
    initPHSlider() {
        const slider = document.getElementById('phSlider');
        const input  = document.getElementById('phLevel');
        const method = document.getElementById('phMethod');
        const hint   = document.getElementById('phHint');
        const color  = (v) => v < 6 ? '#d32f2f' : v <= 7.5 ? '#2e7d32' : '#ff9800';
        // v3.7: el pH arranca SIN DATO. Un 7 puesto por defecto no es una medición y no debe tratarse como tal.
        const sync = () => {
            const has = !!input && input.value.trim() !== '';
            if (method) method.disabled = !has;
            if (hint) hint.style.opacity = has ? '.55' : '1';
            if (slider) { slider.classList.toggle('is-unset', !has); if (!has) slider.style.accentColor = ''; }
        };
        slider?.addEventListener('input', (e) => {
            const v = parseFloat(e.target.value);
            if (input) input.value = v.toFixed(1);
            slider.style.accentColor = color(v);
            sync();
        });
        input?.addEventListener('input', (e) => {
            let v = parseFloat(e.target.value);
            if (!isNaN(v)) { v = Math.max(0, Math.min(14, v)); if (slider) { slider.value = v; slider.style.accentColor = color(v); } }
            sync();
        });
        sync();
    }

    // ── UBICACIÓN: solo cuando el usuario la activa ──────────────────────────
    requestPosition() {
        return new Promise((resolve, reject) => {
            if (!navigator.geolocation) return reject(new Error('Geolocalización no disponible en este navegador'));
            navigator.geolocation.getCurrentPosition(
                (p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude }),
                (e) => reject(new Error(e.code === 1 ? 'Permiso de ubicación denegado' : 'No se pudo obtener la ubicación')),
                { timeout: 15000, maximumAge: 300000 }
            );
        });
    }

    // Pide por adelantado al servidor los datos lentos de la ubicación (WoSIS/SoilGrids); no espera ni muestra errores
    _prefetchGeo(lat, lng) {
        try {
            fetch(this.BASE_URL + '/api/prefetch-profiles', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
                body: JSON.stringify({ lat, lng })
            }).catch(() => {});
        } catch (_) { /* es solo una optimización */ }
    }

    // El nombre del lugar lo resuelve el servidor (con caché y respetando los límites de Nominatim); si falla, se muestran las coordenadas
    async reverseGeocode(lat, lng) {
        try {
            const r = await fetch(this.BASE_URL + '/api/reverse-geocode', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
                body: JSON.stringify({ lat, lng })
            });
            if (!r.ok) return '';
            const j = await r.json();
            return typeof j.region === 'string' ? j.region : '';
        } catch (e) { console.error('Geocoding error:', e); return ''; }
    }

    // Interruptor del formulario: activarlo detecta la ubicación; desactivarlo la borra
    async toggleLocation(on) {
        const box = document.getElementById('enableLocation');
        if (!on) { this.clearLocation(); this.showToast('Ubicación desactivada', 'info'); return; }
        this.showToast('Obteniendo ubicación...', 'info');
        try {
            const { lat, lng } = await this.requestPosition();
            this._prefetchGeo(lat, lng);
            const region = await this.reverseGeocode(lat, lng);
            if (!box?.checked) return;   // la desactivó mientras se obtenía
            this.userLat = lat; this.userLng = lng; this.userRegion = region;
            document.getElementById('userLatitude').value  = lat;
            document.getElementById('userLongitude').value = lng;
            document.getElementById('userRegion').value    = region;
            document.getElementById('locationText').textContent = '📍 ' + (region || `${lat.toFixed(4)}, ${lng.toFixed(4)}`);
            document.getElementById('locationDisplay').classList.remove('hidden');
            this.showToast('📍 Ubicación activada. La IA usará datos de tu región.', 'success');
        } catch (err) {
            if (box) box.checked = false;
            this.clearLocation();
            this.showToast(err.message, 'warning');
        }
    }

    clearLocation() {
        this.userLat = ''; this.userLng = ''; this.userRegion = '';
        ['userLatitude', 'userLongitude', 'userRegion'].forEach((id) => { const el = document.getElementById(id); if (el) el.value = ''; });
        document.getElementById('locationDisplay')?.classList.add('hidden');
    }

    // ── v3.1 · ESCÁNER DE SUELO: pregunta de ubicación (solo con imagen válida) ──
    _esc(s) { return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

    _scanSpinner(text) {
        return `<div style="display:flex;align-items:center;gap:.6rem;"><i class="fas fa-spinner fa-spin" style="color:var(--accent-primary);"></i><strong style="font-size:.9rem;">${text}</strong></div>`;
    }

    // Devuelve {lat, lng, region} o null. Si el usuario ya activó la ubicación en el formulario, no vuelve a preguntar.
    async _resolveScanLocation(r) {
        if (this.userLat && this.userLng) return { lat: this.userLat, lng: this.userLng, region: this.userRegion || '' };
        const out = document.getElementById('scanAnalysis');
        if (!out) return null;
        const wants = await new Promise((resolve) => {
            this._locResolve = resolve;
            out.innerHTML = `<div style="text-align:center;">
                <i class="fas fa-circle-check" style="font-size:2rem;color:var(--accent-primary);display:block;margin-bottom:.4rem;"></i>
                <strong>Suelo detectado: ${this._esc(r.soilType)} (confianza ${this._esc(r.confidence)}%)</strong>
                <p style="margin:.6rem 0 .9rem;font-size:.85rem;color:var(--text-secondary);">¿Quieres usar tu ubicación para que los mapas de suelo (OpenLandMap de 30 m, con SoilGrids de respaldo) y el clima real de NASA aporten datos de tu terreno? Solo se usa para este escaneo.</p>
                <div class="camera-controls">
                    <button type="button" id="scanLocYes" class="capture-btn"><i class="fas fa-location-dot"></i> Sí, usar mi ubicación</button>
                    <button type="button" id="scanLocNo" class="upload-btn">No, continuar sin ubicación</button>
                </div></div>`;
            document.getElementById('scanLocYes').onclick = () => resolve(true);
            document.getElementById('scanLocNo').onclick  = () => resolve(false);
        });
        this._locResolve = null;
        if (wants === 'cancel') return undefined;   // cerró el modal durante la pregunta
        if (!wants) return null;
        out.innerHTML = this._scanSpinner('Obteniendo tu ubicación...');
        try {
            const { lat, lng } = await this.requestPosition();
            this._prefetchGeo(lat, lng);
            return { lat, lng, region: await this.reverseGeocode(lat, lng) };
        } catch (err) {
            this.showToast(err.message + '. Se continúa sin datos satelitales.', 'warning');
            return null;
        }
    }


    // ── CÁMARA ───────────────────────────────────────────────────────────────
    // ── v3.0 · ESCÁNERES DE LA PANTALLA DE INICIO ────────────────────────────
    openScanner(mode = 'soil') {
        this.scanMode = mode;
        this._carryScan = null;
        const plant = mode === 'plant';
        const title = document.getElementById('cameraTitle');
        const hint  = document.getElementById('cameraHint');
        const prev  = document.getElementById('capturedPreview');
        if (title) title.textContent = plant ? 'Escáner de Plantas con IA' : 'Escáner de Suelo con IA';
        if (hint) hint.innerHTML = plant
            ? 'Toma o sube una foto <strong>de la planta completa</strong> (hojas, tallo y, si se ve, su maceta). La IA rechazará fotos que no sean plantas.'
            : 'Toma o sube una foto <strong>del suelo directamente</strong> (tierra, arena, arcilla). La IA rechazará personas, objetos o cualquier cosa que no sea suelo.';
        if (prev) prev.style.display = 'none';
        this.openCameraModal();
    }

    // Abre uno de los dos formularios manuales (suelo o planta); abrir uno cierra el otro
    toggleManualForm(which = 'soil', forceOpen) {
        const panels = { soil: ['manualPanel', 'toggleManualBtn'], plant: ['plantFormPanel', 'togglePlantFormBtn'] };
        const key   = panels[which] ? which : 'soil';
        const panel = document.getElementById(panels[key][0]);
        if (!panel) return;
        const open = typeof forceOpen === 'boolean' ? forceOpen : panel.classList.contains('hidden');
        Object.entries(panels).forEach(([k, [pid, bid]]) => {
            const on = k === key && open;
            document.getElementById(pid)?.classList.toggle('hidden', !on);
            document.getElementById(bid)?.setAttribute('aria-expanded', String(on));
        });
        if (open) setTimeout(() => panel.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
    }

    // Formulario de planta: la misma tubería de 3 etapas del escáner, pero sin foto
    async handlePlantForm(ev) {
        ev.preventDefault();
        if (this._scanBusy) { this.showToast('Ya hay un análisis en curso, espera a que termine.', 'info'); return; }
        const v = (id) => (document.getElementById(id)?.value || '').trim();
        const name = v('plantName');
        if (!name) { this.showToast('Escribe el nombre de la planta o descríbela', 'error'); return; }
        const symptoms = [...document.querySelectorAll('#plantForm input[name="symptom"]:checked')].map((c) => c.value);
        const pot = `material: ${v('plantPotMat')}; tamaño: ${v('plantPotSize')}; drenaje: ${v('plantPotDrain')}`;
        const observations = {
            overall: `Aspecto general: ${v('plantLook')}. Ubicación: ${v('plantPlace')}. Luz: ${v('plantLight')}. Riego: ${v('plantWater')}.`,
            leaves: symptoms.length ? symptoms.join('; ') : 'el usuario no reporta síntomas',
            stemsAndBase: v('plantNotes') || 'sin detalles adicionales',
            substrate: `Maceta (${pot})`,
            pestsSeen: symptoms.filter((s) => /insect|telara|pegajos|polvo|moho/i.test(s)).join('; ') || 'ninguno reportado',
            spots: []
        };
        this._scanBusy = true;
        this.hideResults();
        try {
            this.showLoading(true, '🌿 Etapa 1/3 · IA identificando tu planta...');
            const ident = await this._apiPost('/api/plant-form-identify', { name, desc: v('plantDesc'), place: v('plantPlace') });
            Object.assign(ident, { isPlant: true, potObserved: pot, observations });
            this.showLoading(true, `🩺 Etapa 2/3 · ${ident.commonName || 'Tu planta'}: IA diagnosticando según tus síntomas...`);
            const health = await this._apiPost('/api/scan-plant-health', { ident, source: 'form' });
            this.showLoading(true, '📚 Etapa 3/3 · Consultando la base de datos y generando el informe...');
            const report = await this._apiPost('/api/scan-plant-report', { ident, health });
            this.currentResults = null;
            this.displayPlantReport(ident, health, report, null);
            this.showResults();
        } catch (err) {
            console.error('Plant form error:', err);
            this.displayError(err.message);
            this.showToast(err.message, 'error');
        } finally {
            this.showLoading(false);
            this._scanBusy = false;
        }
    }

    // ── SALUDO CAMBIANTE DEL INICIO ────────────────────────────────────────────
    // Una frase nueva cada vez que entras (con tu nombre) y una línea de apoyo que rota sola unas pocas veces.
    // La hora del día influye solo de vez en cuando (ver GREET_TIME_CHANCE) y las últimas frases mostradas no se repiten.
    async initGreeting() {
        const box = document.getElementById('hubGreet');
        const title = document.getElementById('greetTitle');
        const tip = document.getElementById('greetTip');
        if (!box || !title || !tip) return;

        // Espera el nombre (máx. 1.8 s) para no mostrar el saludo dos veces
        try { await Promise.race([window.userReady, new Promise((r) => setTimeout(r, 1800))]); } catch (_) { /* sin nombre */ }

        const first = String(window.userData?.nombre || '').trim().split(/\s+/)[0].slice(0, 24);
        const reduce = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        const store = {
            get: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } },
            set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* modo privado */ } }
        };
        // Historial de frases recientes (en este navegador)
        const HK = 'ecoscan.greet.hist', TK = 'ecoscan.tip.hist';
        const histGet = (k) => { try { const v = JSON.parse(store.get(k) || '[]'); return Array.isArray(v) ? v.filter((s) => typeof s === 'string') : []; } catch (_) { return []; } };
        const histPush = (k, text) => { store.set(k, JSON.stringify(histGet(k).filter((s) => s !== text).concat(text).slice(-GREET_HISTORY))); };

        const renderTitle = (text) => {
            title.textContent = '';
            const line = document.createElement('span');
            line.className = 'greet-line';
            text.split(' ').forEach((word, i) => {
                if (i) line.appendChild(document.createTextNode(' '));
                const w = document.createElement('span');
                w.className = 'w';
                w.style.setProperty('--i', String(i));
                w.textContent = word;
                line.appendChild(w);
            });
            title.appendChild(line);
            title.setAttribute('aria-label', text);
        };
        let tipTimer = null;
        const showTip = (text) => {
            clearTimeout(tipTimer);
            if (reduce) { tip.textContent = text; return; }
            tip.classList.add('is-out');
            tipTimer = setTimeout(() => { tip.textContent = text; tip.classList.remove('is-out'); }, 450);
        };

        // Titular de esta visita
        const hour0 = new Date().getHours();
        const recent = histGet(HK);
        const timedBefore = recent.slice(-2).filter((t) => { const x = GREET_HEADLINES.find((h) => h.t === t); return x && greetIsTimed(x); }).length;
        const head = greetDrawHeadline(GREET_HEADLINES, greetPeriod(hour0), recent, { allowTimed: timedBefore < 2 });
        histPush(HK, head.t);
        renderTitle(greetFill(head.t, hour0, first));

        // Líneas de apoyo: la primera comparte tema con el titular; las demás no repiten ni lo reciente ni lo ya mostrado en esta visita
        const seen = [];
        let prevTimed = greetIsTimed(head);
        const nextTip = (theme) => {
            const part = greetPeriod(new Date().getHours());   // la hora se revisa en cada cambio por si cambia la franja
            const pick = greetDrawTip(GREET_TIPS, part, histGet(TK).concat(seen), { theme, allowTimed: !prevTimed });
            prevTimed = !!pick.when;
            seen.push(pick.t);
            histPush(TK, pick.t);
            return pick.t;
        };
        tip.textContent = nextTip(head.c);
        box.classList.remove('is-pending');

        // La línea de apoyo rota sola cada 12 s, solo 6 veces, y se detiene si pasas el puntero o no se está viendo la pestaña.
        // Con «reducir movimiento» activado no rota sola.
        if (reduce) return;
        let paused = false, changes = 0;
        box.addEventListener('pointerenter', () => { paused = true; });
        box.addEventListener('pointerleave', () => { paused = false; });
        const timer = setInterval(() => {
            if (paused || document.hidden) return;
            showTip(nextTip(null));
            if (++changes >= 6) clearInterval(timer);
        }, 12000);
    }

    // Fondo animado de la app: auroras, hojas que suben, luciérnagas (menos elementos en celular)
    initBackground() {
        const fx = document.getElementById('bgFx');
        if (!fx) return;
        const mobile = window.innerWidth < 700;
        const rnd = (lo, hi) => lo + Math.random() * (hi - lo);
        const icons = ['fa-leaf', 'fa-seedling', 'fa-leaf', 'fa-spa', 'fa-droplet'];
        let h = '<span class="fx-orb"></span><span class="fx-orb"></span><span class="fx-orb"></span>';
        for (let i = 0; i < (mobile ? 9 : 18); i++) {
            h += `<i class="fas ${icons[i % icons.length]} fx-leaf" style="--x:${rnd(2, 96).toFixed(1)}%;--s:${rnd(1.2, 3.2).toFixed(1)}rem;--o:${rnd(0.14, 0.32).toFixed(2)};--d:${rnd(16, 32).toFixed(1)}s;--dl:-${rnd(0, 30).toFixed(1)}s;--sw:${rnd(-70, 70).toFixed(0)};--r:${rnd(-160, 160).toFixed(0)}"></i>`;
        }
        for (let i = 0; i < (mobile ? 10 : 22); i++) {
            h += `<span class="fx-fly" style="--x:${rnd(0, 100).toFixed(1)}%;--y:${rnd(5, 95).toFixed(1)}%;--s:${rnd(2, 4.5).toFixed(1)}px;--d:${rnd(4, 9).toFixed(1)}s;--dl:-${rnd(0, 9).toFixed(1)}s;--mx:${rnd(-14, 14).toFixed(0)}px"></span>`;
        }
        fx.innerHTML = h;
    }

    // ── v3.3 · ESCÁNER DE PLANTAS: 3 etapas (visión → diagnóstico → BD + informe) ──
    _setResultsMode(mode) {
        this._resultsMode = mode;
        document.getElementById('resultsContainer')?.classList.toggle('is-wide', this._isWide());   // ancho extra solo en laptop y tablet
        const t = document.querySelector('#resultsContainer .results-header h3');
        if (t) t.textContent = mode === 'plant' ? 'Informe de la Planta' : 'Diagnóstico Completo IA';
        const pdf = document.getElementById('downloadPdfBtn');
        if (pdf) pdf.innerHTML = `<i class="fas fa-file-pdf"></i> <span class="btn-long">${mode === 'plant' ? 'Descargar Informe de la Planta PDF' : 'Descargar Diagnóstico PDF'}</span><span class="btn-short">PDF</span>`;
    }

    // ¿Responde el servidor de EcoScan? (cualquier respuesta HTTP cuenta; solo un corte total devuelve false)
    async _serverReachable() {
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), 4000);
        try { await fetch(this.BASE_URL + '/api/keepalive', { cache: 'no-store', signal: ctl.signal }); return true; }
        catch (_) { return false; }
        finally { clearTimeout(timer); }
    }

    // POST a la API. Se distinguen tres cortes que antes se veían igual:
    //  · la solicitud no se pudo armar (error del navegador)  · el servidor de EcoScan no responde  · la IA/internet fallan
    async _apiPost(path, payload) {
        let body;
        try { body = JSON.stringify({ lang: this.currentLang, ...payload }); }
        catch (err) {
            console.error('No se pudo preparar la solicitud a', path, err);
            throw new Error('No se pudo preparar la solicitud (' + (err && err.message || err) + '). Recarga la página e inténtalo de nuevo.');
        }
        let resp;
        try {
            resp = await fetch(this.BASE_URL + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body });
        } catch (err) {
            console.error('La solicitud a', path, 'no obtuvo respuesta:', err);
            throw new Error((await this._serverReachable()) ? CONN_MSG : SERVER_MSG);
        }
        const d = await resp.json().catch(() => ({}));
        if (resp.status === 401 && d.code === 'auth') { this._sessionExpired(); throw new Error(d.error || 'Tu sesión expiró. Inicia sesión de nuevo.'); }
        if (!resp.ok || d.error) throw new Error(d.error || CONN_MSG);
        return d;
    }

    // La sesión caducó (o se cerró en otra pestaña): se avisa y se vuelve a la pantalla de acceso
    _sessionExpired() {
        if (this._sessionGone) return;
        this._sessionGone = true;
        this.showToast('Tu sesión expiró. Te llevamos al inicio de sesión…', 'warning');
        setTimeout(() => { window.location.href = 'login.html'; }, 1800);
    }

    _aiAlert(msg) {
        const conn = msg === CONN_MSG, down = msg === SERVER_MSG;
        const icon  = conn ? 'fa-wifi' : down ? 'fa-server' : 'fa-circle-exclamation';
        const title = conn ? CONN_TITLE : down ? SERVER_TITLE : 'No se pudo completar el análisis';
        const text  = conn ? CONN_HINT : down ? SERVER_HINT : msg;
        return `<div style="display:flex;align-items:flex-start;gap:.6rem;padding:.7rem .9rem;border-radius:12px;background:rgba(229,57,53,.12);border:1px solid var(--error);text-align:left;"><i class="fas ${icon}" style="color:var(--error);margin-top:.2rem;"></i><div><strong style="color:var(--error);">${title}</strong><p style="font-size:.85rem;color:var(--text-secondary);margin-top:.2rem;">${this._esc(text)}</p></div></div>`;
    }

    _chatContext() {
        if (this._resultsMode === 'plant') return this.plantCtx || '';
        if (this.soilCtx) return this.soilCtx;
        const d = this.currentData;
        return d ? `Suelo:${d.soilType}, pH:${d.ph}, Región:${d.region}` : '';
    }

    async analyzePlantImage(dataUrl) {
        const out      = document.getElementById('scanAnalysis');
        const pv       = document.getElementById('capturedPreview');
        const applyBtn = document.getElementById('applyScanResult');
        if (!out) return;
        const e = (s) => this._esc(s);
        if (pv) { pv.src = dataUrl; pv.style.display = 'block'; }
        if (applyBtn) applyBtn.style.display = 'none';
        document.getElementById('scanResult')?.classList.remove('hidden');
        try {
            out.innerHTML = this._scanSpinner('Etapa 1/3 · IA de visión identificando la planta y leyendo manchas, formas y colores...');
            const ident = await this._apiPost('/api/scan-plant', { image: dataUrl, region: this.userRegion });
            if (!ident.isPlant) {
                if (pv) pv.style.display = 'none';
                out.innerHTML = `<div style="text-align:center;padding:.75rem 0;"><i class="fas fa-ban" style="font-size:2.5rem;color:var(--error);margin-bottom:.5rem;display:block;"></i><strong style="color:var(--error);">No se detectó una planta en la imagen</strong><p style="margin-top:.5rem;font-size:.85rem;color:var(--text-secondary);">${e(ident.rejection_reason || 'Apunta directamente a la planta (hojas, tallo o ejemplar completo).')}</p></div>`;
                return;
            }
            out.innerHTML = this._scanSpinner(`Etapa 2/3 · ${e(ident.commonName)}: IA diagnosticando enfermedades y problemas...`);
            const health = await this._apiPost('/api/scan-plant-health', { ident });
            out.innerHTML = this._scanSpinner('Etapa 3/3 · Consultando la base de datos y generando el informe...');
            const report = await this._apiPost('/api/scan-plant-report', { ident, health });
            this.closeCameraModal();
            this.hideResults();
            this.currentResults = null;
            this.displayPlantReport(ident, health, report, dataUrl);
            this.showResults();
            this.showToast('✅ Informe de la planta generado', 'success');
        } catch (err) {
            console.error('Plant scan error:', err);
            if (pv) pv.style.display = 'none';
            out.innerHTML = this._aiAlert(err.message);
            this.showToast(err.message, 'error');
        }
    }

    // ── KIT DE INFORMES (compartido por el informe de suelo y el de planta) ──────
    // Tres presentaciones, según la pantalla:
    //  · Celular (≤ 640 px): resumen primero, secciones plegables con vista previa y chips que se deslizan.
    //  · Laptop y tablet (≥ 768 px de ancho y ≥ 540 px de alto, la misma regla que usa el resto de la app):
    //    panel amplio con cifras clave, índice fijo, texto más grande y columnas equilibradas (v3.9).
    //  · Intermedio (p. ej. un teléfono girado, de poca altura): la versión compacta de escritorio de siempre.
    _isMobile() { return !!(window.matchMedia && window.matchMedia('(max-width: 640px)').matches); }
    _isWide()   { return !!(window.matchMedia && window.matchMedia('(min-width: 768px) and (min-height: 540px)').matches); }
    _vpMode()   { return this._isMobile() ? 'mobile' : this._isWide() ? 'wide' : 'mid'; }

    // Al cambiar de presentación (girar el teléfono o la tablet, cambiar el tamaño de la ventana) se reconstruye el
    // informe visible con la que corresponde, sin perder la conversación con EcoChat.
    _watchViewport() {
        if (this._vpWatch || !window.matchMedia) return;
        this._vpWatch = true;
        let mode = this._vpMode();
        const onChange = () => {
            const now = this._vpMode();
            if (now === mode) return;
            mode = now;
            if (!this._rerender || document.getElementById('resultsContainer')?.classList.contains('hidden')) return;
            const chat = document.getElementById('chatMessages')?.innerHTML;
            const typed = document.getElementById('chatInput')?.value;
            const openChat = document.getElementById('rp-chat')?.open;
            try { this._rerender(); } catch (err) { console.error('No se pudo reacomodar el informe:', err); return; }
            if (chat) { const m = document.getElementById('chatMessages'); if (m) m.innerHTML = chat; }
            if (typed) { const i = document.getElementById('chatInput'); if (i) i.value = typed; }
            if (openChat) { const c = document.getElementById('rp-chat'); if (c) c.open = true; }
        };
        ['(max-width: 640px)', '(min-width: 768px) and (min-height: 540px)'].forEach((q) => {
            const mq = window.matchMedia(q);
            if (mq.addEventListener) mq.addEventListener('change', onChange); else if (mq.addListener) mq.addListener(onChange);
        });
        const two = window.matchMedia('(min-width: 900px) and (min-height: 540px)');      // una ↔ dos columnas
        const onCols = () => { if (this._vpMode() === 'wide') this._rpRebalance(document.getElementById('resultsContent')); };
        if (two.addEventListener) two.addEventListener('change', onCols); else if (two.addListener) two.addListener(onCols);
    }

    _rpGauge(score, color, size) {
        return `<div class="rp-gauge" style="--p:${score};--c:${color}${size ? ';--size:' + size + 'px' : ''}" role="img" aria-label="${score} de 100"><div class="rp-gauge-in"><b>${score}</b><small>/100</small></div></div>`;
    }

    // Sección plegable. o = { tag, prev, open, wide }. En celular solo se abre la que pide open:true.
    _rpSec(id, icon, title, o, body) {
        o = o || {};
        const mobile = this._isMobile();
        const open = mobile ? !!o.open : o.open !== false;
        if (this._rpNav) this._rpNav.push({ id, icon, label: o.nav || title });      // alimenta el índice de laptop y tablet
        return `<details class="rp-sec${o.wide ? ' rp-sec--wide' : ''}" id="rp-${id}"${open ? ' open' : ''}>
            <summary><span class="rp-ico"><i class="fas ${icon}"></i></span><span class="rp-ttl">${title}${o.prev ? `<span class="rp-prev">${this._esc(o.prev)}</span>` : ''}</span>${o.tag ? `<span class="rp-tag">${o.tag}</span>` : ''}<i class="fas fa-chevron-down rp-chev"></i></summary>
            <div class="rp-body">${mobile && o.tag ? `<div class="rp-tagrow">${o.tag}</div>` : ''}${body}</div>
        </details>`;
    }

    // Acomoda las secciones del informe.
    //  · Celular: una sola columna.
    //  · Laptop y tablet: dos columnas EQUILIBRADAS por contenido (cada sección va a la columna menos cargada, sin
    //    huecos) y, en tablet vertical, una columna con el orden original (--o). Las secciones «wide» ocupan todo el
    //    ancho y separan los grupos.
    //  · Intermedio: dos columnas de CSS (columns) a partir de 980 px, como siempre.
    _rpLayout(secs) {
        if (this._isMobile()) return `<div class="rp-secs">${secs.join('')}</div>`;
        const wide = this._isWide();
        const out = []; let run = [], n = 0;
        const flush = () => {
            if (!run.length) return;
            if (run.length === 1) out.push(run[0]);
            else if (wide) out.push(this._rpBalance(run, n - run.length));
            else out.push(`<div class="rp-cols">${run.join('')}</div>`);
            run = [];
        };
        secs.forEach((s) => { n++; if (s.startsWith('<details class="rp-sec rp-sec--wide"')) { flush(); out.push(s); } else run.push(s); });
        flush();
        return `<div class="rp-secs">${out.join('')}</div>`;
    }

    _rpBalance(run, first) {
        const weight = (h) => h.replace(/<[^>]*>/g, '').length + 260;      // texto visible + el alto de la cabecera
        const cols = [[], []], load = [0, 0];
        run.forEach((h, i) => {
            const c = load[0] <= load[1] ? 0 : 1;
            cols[c].push(h.replace(/^<details /, `<details style="--o:${first + i}" `));
            load[c] += weight(h);
        });
        return `<div class="rp-cols rp-cols--bal">${cols.map((c) => `<div class="rp-col">${c.join('')}</div>`).join('')}</div>`;
    }

    // Reparte las tarjetas entre las dos columnas con sus ALTURAS REALES: prueba todas las combinaciones (conservando el
    // orden de lectura dentro de cada columna) y elige la que deja las columnas más parejas. Solo cuando hay dos columnas.
    _rpRebalance(box) {
        (box || document).querySelectorAll('.rp-cols--bal').forEach((wrap) => {
            if (getComputedStyle(wrap).display !== 'grid') return;                  // una sola columna: no hay nada que repartir
            const cols = wrap.querySelectorAll(':scope > .rp-col');
            if (cols.length !== 2) return;
            const secs = [...wrap.querySelectorAll('.rp-sec')].sort((a, b) => parseFloat(a.style.getPropertyValue('--o')) - parseFloat(b.style.getPropertyValue('--o')));
            const n = secs.length;
            if (n < 3 || n > 12) return;
            const gap = parseFloat(getComputedStyle(cols[0]).rowGap) || 0;
            const hs = secs.map((x) => x.offsetHeight);
            let best = null;
            for (let m = 0; m < (1 << (n - 1)); m++) {                              // bit i = 1 → la sección i+1 va a la derecha
                let l = hs[0], r = 0, nl = 1, nr = 0;
                for (let i = 1; i < n; i++) { if (m & (1 << (i - 1))) { r += hs[i]; nr++; } else { l += hs[i]; nl++; } }
                const cost = Math.max(l + gap * (nl - 1), r + gap * Math.max(nr - 1, 0));
                if (!best || cost < best.cost - 1) best = { cost, m };
            }
            secs.forEach((x, i) => (i > 0 && (best.m & (1 << (i - 1))) ? cols[1] : cols[0]).appendChild(x));
        });
    }

    // Cifras clave (solo laptop y tablet). Cada dato: [icono, etiqueta, valor, nota HTML ya escapada, color].
    _rpKpis(items) {
        const list = (items || []).filter(Boolean);
        if (!this._isWide() || !list.length) return '';
        return `<section class="rp-kpis" data-n="${list.length}" style="--n:${list.length}" aria-label="Cifras clave">${list.map(([ic, label, value, sub, color]) =>
            `<div class="rp-kpi"${color ? ` style="--c:${color}"` : ''}><div class="rp-kpi-k"><span aria-hidden="true">${ic}</span>${this._esc(label)}</div><div class="rp-kpi-v${String(value).length > 14 ? ' rp-kpi-v--txt' : ''}">${this._esc(value)}</div>${sub ? `<div class="rp-kpi-s">${sub}</div>` : ''}</div>`).join('')}</section>`;
    }

    // Índice fijo de secciones (solo laptop y tablet)
    _rpIndex() {
        const items = this._rpNav || [];
        if (!this._isWide() || items.length < 3) return '';
        return `<nav class="rp-nav" aria-label="Ir a una sección del informe">${items.map((x) =>
            `<button type="button" data-goto="rp-${this._esc(x.id)}"><i class="fas ${this._esc(x.icon)}" aria-hidden="true"></i><span>${this._esc(x.label)}</span></button>`).join('')}</nav>`;
    }

    _rpList(items) {
        const li = (items || []).filter(Boolean).map((x) => `<li>${this._esc(x)}</li>`).join('');
        return li ? `<ul class="rp-list">${li}</ul>` : '';
    }

    // Grupo con subtítulo y lista de viñetas
    _rpGroup(label, items) {
        const ul = this._rpList(items);
        return ul ? `<div class="rp-group"><div class="rp-subl">${label}</div>${ul}</div>` : '';
    }

    // Celdas «etiqueta / valor». label puede llevar HTML propio (iconos, chips); value siempre se escapa.
    _rpCells(items) {
        const list = (items || []).filter((x) => x && x[2]);
        return list.length ? `<div class="rp-cells">${list.map(([ic, label, value, wide]) => `<div class="rp-cell${wide ? ' rp-cell--wide' : ''}"><div class="k">${ic} ${label}</div><div class="v">${this._esc(String(value).replace(/^\S/, (c) => c.toUpperCase()))}</div></div>`).join('')}</div>` : '';
    }

    _rpRows(items) {
        const list = (items || []).filter((x) => x && x[1]);
        return list.length ? `<div class="rp-rows">${list.map(([k, v]) => `<div class="rp-row"><span class="rp-k">${k}</span><span>${this._esc(v)}</span></div>`).join('')}</div>` : '';
    }

    _rpFirst(label, items, openId, openLabel) {
        const top = (items || []).filter(Boolean).slice(0, 3);
        if (!top.length) return '';
        return `<div class="rp-first"><h5>${label}</h5><ol>${top.map((x) => `<li>${this._esc(x)}</li>`).join('')}</ol><button type="button" data-open="${openId}">${openLabel} →</button></div>`;
    }

    // Botones «Ver …» del resumen móvil: abren la sección y se desplazan hasta ella
    _bindReportActions(box) {
        box.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => {
            const t = document.getElementById(b.dataset.open);
            if (t) { t.open = true; t.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
        }));
        this._bindIndex(box);
    }

    // Índice de secciones: abre y lleva a la sección; resalta la que se está leyendo
    _bindIndex(box) {
        if (this._navObs) { this._navObs.disconnect(); this._navObs = null; }
        const nav = box.querySelector('.rp-nav');
        if (!nav) return;
        const art = nav.closest('.rp');
        const hdr = document.querySelector('.header');
        if (art) art.style.setProperty('--rp-top', ((hdr && hdr.offsetHeight) || 72) + 'px');
        // difuminado en el borde que tiene más botones por mostrar (el índice se desliza si no cabe)
        const clip = () => { nav.classList.toggle('is-clip-r', nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 4); nav.classList.toggle('is-clip-l', nav.scrollLeft > 4); };
        nav.addEventListener('scroll', clip, { passive: true });
        if (this._navClip) window.removeEventListener('resize', this._navClip);
        this._navClip = clip; window.addEventListener('resize', clip);
        clip();
        const btns = [...nav.querySelectorAll('[data-goto]')];
        const mark = (id) => btns.forEach((b) => { const on = b.dataset.goto === id; b.classList.toggle('is-on', on); if (on) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current'); });
        btns.forEach((b) => b.addEventListener('click', () => {
            const t = document.getElementById(b.dataset.goto);
            if (!t) return;
            t.open = true;
            t.scrollIntoView({ behavior: 'smooth', block: 'start' });
            mark(b.dataset.goto);
            b.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
        }));
        if (!('IntersectionObserver' in window)) return;
        const seen = new Map();
        this._navObs = new IntersectionObserver((entries) => {
            entries.forEach((en) => seen.set(en.target.id, en.isIntersecting ? en.boundingClientRect.top : null));
            const vis = [...seen.entries()].filter(([, t]) => t !== null).sort((x, y) => x[1] - y[1]);
            if (vis.length) mark(vis[0][0]);
        }, { rootMargin: '-25% 0px -60% 0px' });
        btns.forEach((b) => { const t = document.getElementById(b.dataset.goto); if (t) this._navObs.observe(t); });
    }

    displayPlantReport(id, h, res, dataUrl) {
        const box = document.getElementById('resultsContent');
        if (!box) return;
        this._setResultsMode('plant');
        this._rerender = () => this.displayPlantReport(id, h, res, dataUrl);
        this._watchViewport();
        const mobile = this._isMobile(), wide = this._isWide();
        this._rpNav = [];
        const e    = (s) => this._esc(s);
        const cap  = (s) => { s = String(s ?? ''); return s.charAt(0).toUpperCase() + s.slice(1); };
        // Los datos llegan de una IA: se garantiza la forma antes de pintar (un texto donde va una lista ya no rompe el informe)
        const L = (v) => Array.isArray(v) ? v.filter((x) => x != null && x !== '') : (typeof v === 'string' && v.trim() ? [v.trim()] : []);
        const O = (v) => (v && typeof v === 'object' && !Array.isArray(v)) ? v : {};
        id = O(id); h = O(h); res = O(res);
        const rep  = O(res.report), care = O(rep.careNeeds), pot = { ...O(rep.potRecommendation), extraTips: L(O(rep.potRecommendation).extraTips) };
        const plan = { thisWeek: L(O(rep.carePlan).thisWeek), thisMonth: L(O(rep.carePlan).thisMonth), ongoing: L(O(rep.carePlan).ongoing) };
        const dbm  = L(res.databaseMatches).filter((m) => m && typeof m === 'object'), obs = O(id.observations);
        const probs = L(h.problems).map((p) => (p && typeof p === 'object') ? { ...p, treatment: L(p.treatment) } : { name: String(p), treatment: [] }).filter((p) => p.name);
        const score = Math.round(Math.max(0, Math.min(100, Number(h.healthScore) || 0)));
        const hc   = { 'sana': '#6ab04c', 'estrés leve': '#ff9800', 'enferma': '#f44336', 'crítica': '#b71c1c' }[String(h.healthStatus || '').toLowerCase()] || '#8bc34a';
        const sevC = (p) => ({ leve: '#6ab04c', moderada: '#ff9800', grave: '#f44336' }[String(p.severity || '').toLowerCase()] || '#ff9800');
        const chip = (t) => t ? `<span class="rp-chip">${e(t)}</span>` : '';
        const status = cap(h.healthStatus || 'N/A');
        const obsText   = [obs.overall, obs.leaves, obs.stemsAndBase].map((x) => String(x ?? '').trim().replace(/[.\s]+$/, '')).filter(Boolean).map((x) => x.charAt(0).toUpperCase() + x.slice(1)).join('. ');
        const urgent    = L(h.urgentActions);
        const warn      = L(rep.warnings);
        // El PDF usa estos mismos datos ya saneados
        this.plantReport = { id, h: { ...h, problems: probs, urgentActions: urgent },
                             res: { ...res, report: { ...rep, careNeeds: care, potRecommendation: pot, carePlan: plan, warnings: warn }, databaseMatches: dbm }, dataUrl };
        const careCells = this._rpCells([['☀️', 'Luz', care.light], ['💧', 'Riego', care.watering], ['🌫️', 'Humedad', care.humidity], ['🌡️', 'Temperatura', care.temperature], ['🌱', 'Sustrato', care.substrate], ['🧪', 'Abono', care.fertilizing], ['✂️', 'Poda', care.pruning]].map((c) => [...c, true]));
        const potCells  = this._rpCells([['🏺', 'Material', pot.material], ['📏', 'Tamaño', pot.size], ['⬇️', 'Profundidad', pot.depth], ['🕳️', 'Drenaje', pot.drainage], ['🌱', 'Sustrato', pot.substrate], ['🔄', 'Trasplante', pot.repotting]].map((c) => [...c, true]));
        const planGroups = [['📅 Esta semana', plan.thisWeek], ['🗓️ Este mes', plan.thisMonth], ['🔁 De forma continua', plan.ongoing]];
        const hasPlan   = planGroups.some((g) => (g[1] || []).filter(Boolean).length) || warn.length;
        const idProv    = provChip(dataUrl ? 'ai' : 'declared', dataUrl ? 'Identificada por IA (foto)' : 'Declarada por ti + IA');
        const hProv     = provChip(dataUrl ? 'ai' : 'declared', dataUrl ? 'Diagnóstico IA (foto)' : 'Síntomas declarados + IA');

        // ── Encabezado ──
        const chips = [id.family ? 'Familia ' + id.family : '', id.plantType, id.lifeCycle, id.habitat].map(chip).join('');
        const hero = `<header class="rp-hero">
            <div class="rp-media">${dataUrl ? `<img src="${e(dataUrl)}" alt="Foto de la planta">` : '<i class="fas fa-leaf"></i>'}</div>
            <div class="rp-id">
                <div class="rp-id-top">
                    <h4 class="rp-name">${e(id.commonName)}</h4>
                    <p class="rp-sci">${e(id.scientificName)}</p>
                    <p class="rp-meta">${idProv} · Confianza de la IA: <strong>${e(id.confidence ?? 'N/A')}%</strong>${id.origin ? ' · Origen: ' + e(id.origin) : ''}</p>
                </div>
                <div class="rp-chips">${chips}</div>
            </div>
            ${mobile ? '' : `<div class="rp-score">${this._rpGauge(score, hc)}<span class="rp-level" style="--c:${hc}">${e(status)}</span></div>`}
        </header>`;
        const lead = id.description ? `<p class="rp-lead">${e(id.description)}</p>` : '';

        // ── Resumen para celular ──
        const glance = mobile ? `<section class="rp-glance" aria-label="Resumen">
            <div class="rp-glance-top">${this._rpGauge(score, hc, 68)}<div><span class="rp-level" style="--c:${hc}">${e(status)}</span>${h.summary ? `<p>${e(h.summary)}</p>` : ''}</div></div>
            <div class="rp-stats">
                <div class="rp-stat"><small>Problemas</small><b>${probs.length ? probs.length + ' detectado' + (probs.length > 1 ? 's' : '') : 'Ninguno'}</b></div>
                <div class="rp-stat"><small>Confianza IA</small><b>${e(id.confidence ?? 'N/A')}%</b></div>
            </div>
            ${this._rpFirst('⚡ Primero haz esto', urgent, 'rp-health', 'Ver estado de salud')}
        </section>` : '';

        // ── Secciones ──
        const secs = [];
        if (mobile && id.description) secs.push(this._rpSec('about', 'fa-leaf', 'Sobre la planta', { prev: id.scientificName || '' }, `<p>${e(id.description)}</p>`));
        secs.push(this._rpSec('health', 'fa-heart-pulse', 'Estado de salud',
            { nav: 'Salud', tag: hProv, prev: `${status} · ${score}/100`, open: true },
            `${mobile ? '' : `<div class="rp-line"><span class="rp-pill" style="--c:${hc}">${e(status)}</span><div class="rp-bar" style="--c:${hc}"><span><i style="width:${score}%"></i></span><small>${score}/100</small></div></div>`}
             ${!mobile && h.summary ? `<p>${e(h.summary)}</p>` : ''}
             ${this._rpRows([['Lo observado', obsText]])}
             ${mobile ? this._rpGroup('⚡ Más acciones inmediatas', urgent.slice(3)) : this._rpGroup('⚡ Acciones inmediatas', urgent)}`));

        secs.push(this._rpSec('problems', 'fa-bug', 'Enfermedades y problemas',
            { nav: 'Problemas', prev: probs.length ? probs.map((p) => p.name).join(' · ') : 'Sin problemas visibles', open: mobile ? !!probs.length : true },
            probs.length ? probs.map((p) => `<div class="rp-issue" style="--c:${sevC(p)}">
                <div class="rp-issue-h"><strong>${e(p.name)}</strong>${p.type ? `<span class="rp-tagsm">${e(p.type)}</span>` : ''}<span class="rp-issue-sev">${e(cap(p.severity))}${p.probability != null ? ' · ' + e(p.probability) + '%' : ''}</span></div>
                ${p.evidence ? `<p>${e(p.evidence)}</p>` : ''}
                ${(p.treatment || []).filter(Boolean).length ? `<p class="fix"><i class="fas fa-shield-alt"></i>${e((p.treatment || []).map((x) => String(x).replace(/[.\s]+$/, '')).filter(Boolean).join('. '))}.</p>` : ''}
                ${p.prevention ? `<p><strong>Prevención:</strong> ${e(p.prevention)}</p>` : ''}
            </div>`).join('') : '<div class="rp-callout">✅ No se observan enfermedades ni plagas en la fotografía.</div>'));

        const quick = L(id.quickCare);
        secs.push(this._rpSec('care', 'fa-seedling', 'Cuidados recomendados',
            { nav: 'Cuidados', tag: dbm.length ? provChip('ref', 'Base de datos de plantas + IA') : provChip('ai'), prev: care.watering ? 'Riego: ' + care.watering : (care.light ? 'Luz: ' + care.light : ''), wide: false },
            `${rep.summary ? `<p class="rp-muted">${e(rep.summary)}</p>` : ''}
             ${careCells || (quick.length ? this._rpList(quick) : '<p class="rp-muted">No se pudo generar el informe de cuidados.</p>')}
             ${dbm.length ? `<p class="rp-small rp-muted">📚 Referencia de la base de datos: ${e(dbm.map((m) => m.name + ' (' + m.match_score + '/100)').join(' · '))}</p>` : ''}`));

        if (potCells) {
            secs.push(this._rpSec('pot', 'fa-fill-drip', 'Maceta recomendada',
                { nav: 'Maceta', prev: pot.material ? [pot.material, pot.size].filter(Boolean).join(' · ') : '' },
                `${potCells}
                 ${pot.currentPotAssessment ? `<div class="rp-callout"><strong>Tu maceta actual:</strong> ${e(pot.currentPotAssessment)}</div>` : ''}
                 ${this._rpList(pot.extraTips)}`));
        }

        if (hasPlan) {
            secs.push(this._rpSec('plan', 'fa-calendar-check', 'Plan de cuidado',
                { nav: 'Plan', prev: plan.thisWeek && plan.thisWeek[0] ? 'Esta semana: ' + plan.thisWeek[0] : '' },
                `${planGroups.map((g) => this._rpGroup(g[0], g[1])).join('')}
                 ${warn.length ? `<div class="rp-callout rp-callout--warn"><strong>⚠️ Ten en cuenta:</strong> ${e(warn.map((x) => String(x).replace(/[.\s]+$/, '')).join('. '))}.</div>` : ''}`));
        }

        secs.push(this._chatSection('plant'));

        const short = (t, n = 44) => { t = String(t ?? '').trim(); if (!t) return ''; const cut = t.split(/;\s|\s\(|\.\s/)[0]; return cut.length > n ? cut.slice(0, n - 1).trimEnd() + '…' : cut; };
        const kpis = this._rpKpis([
            ['💚', 'Salud', status, score + '/100', hc],
            ['🩺', 'Problemas', probs.length ? String(probs.length) : 'Ninguno', probs.length ? e(short(probs[0].name, 34)) : 'sin daños visibles', probs.length ? sevC(probs[0]) : '#6ab04c'],
            ['🎯', 'Confianza IA', (id.confidence != null && id.confidence !== '' ? id.confidence + '' : 'N/A') + (id.confidence != null && id.confidence !== '' ? ' %' : ''), dataUrl ? 'identificada por foto' : 'según lo que escribiste'],
            care.watering ? ['💧', 'Riego', short(care.watering), ''] : null,
            care.light ? ['☀️', 'Luz', short(care.light), ''] : null,
            care.temperature ? ['🌡️', 'Temperatura', short(care.temperature), ''] : null
        ]);
        const layout = this._rpLayout(secs), index = this._rpIndex();
        this._rpNav = null;
        box.innerHTML = `<article class="rp rp--plant${wide ? ' rp--wide' : ''}">${hero}${mobile ? glance : lead}${kpis}${index}${layout}</article>`;
        if (wide) this._rpRebalance(box);
        this.plantCtx = `Planta: ${id.commonName} (${id.scientificName}), familia ${id.family}, tipo ${id.plantType}. Salud: ${h.healthStatus} (${score}/100). Problemas: ${probs.map((p) => p.name).join(', ') || 'ninguno'}. Maceta observada: ${id.potObserved || 'no visible'}.`;
        this._bindChat();
        this._bindReportActions(box);
    }

    // Miniatura cuadrada (recorte central) para la portada del PDF
    _squareThumb(dataUrl, size = 400) {
        if (!dataUrl) return Promise.resolve(null);
        return new Promise((resolve) => {
            const im = new Image();
            im.onload = () => {
                const s = Math.min(im.width, im.height), c = document.createElement('canvas');
                c.width = c.height = size;
                c.getContext('2d').drawImage(im, (im.width - s) / 2, (im.height - s) / 2, s, s, 0, 0, size, size);
                resolve(c.toDataURL('image/jpeg', 0.85));
            };
            im.onerror = () => resolve(null);
            im.src = dataUrl;
        });
    }

    // ── PDF del informe de la planta ─────────────────────────────────────────
    async downloadPlantPDF() {
        const pr = this.plantReport;
        if (!pr) { this.showToast('Primero analiza una planta para descargar el informe', 'warning'); return; }
        if (typeof window.jspdf === 'undefined') { this.showToast('Librería PDF no disponible. Verifica tu conexión.', 'error'); return; }
        this.showToast('Generando PDF...', 'info');
        try {
            const { id, h, res, dataUrl } = pr;
            const photo = await this._squareThumb(dataUrl);
            const { jsPDF } = window.jspdf;
            const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
            const rep = res.report || {}, care = rep.careNeeds || {}, pot = rep.potRecommendation || {}, plan = rep.carePlan || {};
            const obs = id.observations || {}, probs = h.problems || [], dbm = res.databaseMatches || [];
            const now = new Date();
            const fecha = now.toLocaleDateString('es-MX', { year: 'numeric', month: 'long', day: 'numeric' });
            const cap = (s) => { s = String(s ?? ''); return s.charAt(0).toUpperCase() + s.slice(1); };
            const trim = (s) => String(s ?? '').replace(/[.\s]+$/, '');
            const K = makePdfKit(doc, { footerLabel: 'EcoScan - Informe de Planta con IA', fecha });
            const { C, ML, CW, PW } = K;
            const score = Math.round(Math.max(0, Math.min(100, Number(h.healthScore) || 0)));
            const hcol = ({ 'sana': C.dark, 'estrés leve': C.warn, 'enferma': C.bad, 'crítica': [150, 20, 20] })[String(h.healthStatus || '').toLowerCase()] || C.mut;
            const sevCol = (p) => ({ leve: C.dark, moderada: C.warn, grave: C.bad })[String(p.severity || '').toLowerCase()] || C.warn;

            // ── Portada ──
            K.banner('Informe de Planta con IA');
            let y = K.k.y;
            K.rect(ML, y, CW, 44, 4, C.light);
            if (photo) doc.addImage(photo, 'JPEG', ML + 4, y + 4, 36, 36);
            const tx = photo ? ML + 46 : ML + 6, tw = CW - (tx - ML) - 5;
            K.font('bold', 15, C.txt); doc.text(K.fit(id.commonName || 'Planta', tw, 'bold', 15), tx, y + 11);
            K.font('italic', 10, C.mut); doc.text(K.fit(id.scientificName, tw, 'italic', 10), tx, y + 17);
            K.font('normal', 9, C.txt);
            doc.text(K.fit('Familia: ' + (id.family || 'N/A'), tw), tx, y + 24);
            doc.text(K.fit('Tipo: ' + (id.plantType || 'N/A') + (id.habitat ? ' | ' + id.habitat : ''), tw), tx, y + 29.5);
            doc.text(K.fit('Para: ' + (window.userData?.nombre || 'Usuario') + ' | ' + fecha, tw), tx, y + 35);
            const hl = K.T((h.healthStatus || 'N/A').toUpperCase()) + '  ' + score + '/100';
            K.font('bold', 8.5, C.white);
            const hw = doc.getTextWidth(hl) + 8;
            K.rect(tx, y + 37, hw, 5.6, 2, hcol); doc.text(hl, tx + 4, y + 41);
            K.k.y = y + 52;

            // ── 1 · Identificación ──
            K.section('Identificación de la planta');
            K.para(id.description);
            K.kv('Confianza de la IA', (id.confidence ?? 'N/A') + '%');
            K.kv('Ciclo de vida', id.lifeCycle);
            K.kv('Origen', id.origin);
            K.gap(2);

            // ── 2 · Estado de salud ──
            K.section('Estado de salud');
            K.para(h.summary);
            const seen = [obs.overall, obs.leaves, obs.stemsAndBase].map(trim).filter(Boolean).map(cap).join('. ');
            K.kv('Lo observado', seen ? seen + '.' : '');
            const urgent = (h.urgentActions || []).filter(Boolean);
            if (urgent.length) { K.gap(1); K.sub('Acciones inmediatas'); K.bullets(urgent); }
            K.gap(2);

            // ── 3 · Enfermedades y problemas ──
            K.section('Enfermedades y problemas', 28);
            if (!probs.length) K.para('No se observan enfermedades ni plagas en la fotografía.');
            probs.forEach((p) => K.card({
                title: p.name, tag: p.severity ? cap(p.severity) + (p.probability != null ? ' ' + p.probability + '%' : '') : '', tagColor: sevCol(p), accent: sevCol(p),
                meta: cap(p.type),
                blocks: [
                    { text: p.evidence, color: C.mut },
                    { text: (p.treatment || []).length ? 'Tratamiento: ' + (p.treatment || []).map(trim).filter(Boolean).join('. ') + '.' : '', color: C.fix },
                    { text: p.prevention ? 'Prevención: ' + p.prevention : '', color: C.mut }
                ]
            }));
            K.gap(1);

            // ── 4 · Cuidados recomendados ──
            K.section('Cuidados recomendados');
            K.para(rep.summary, { color: C.mut });
            const careRows = [['Luz', care.light], ['Riego', care.watering], ['Humedad', care.humidity], ['Temperatura', care.temperature], ['Sustrato', care.substrate], ['Abono', care.fertilizing], ['Poda', care.pruning]];
            if (careRows.some((r) => r[1])) careRows.forEach(([k, v]) => K.kv(k, v));
            else K.bullets(id.quickCare);
            if (dbm.length) K.para('Referencia de la base de datos: ' + dbm.map((m) => m.name + ' (' + m.match_score + '/100)').join(' | '), { size: 8, color: C.mut });
            K.gap(2);

            // ── 5 · Maceta recomendada ──
            const potRows = [['Material', pot.material], ['Tamaño', pot.size], ['Profundidad', pot.depth], ['Drenaje', pot.drainage], ['Sustrato', pot.substrate], ['Trasplante', pot.repotting], ['Tu maceta actual', pot.currentPotAssessment]];
            if (potRows.some((r) => r[1])) {
                K.section('Maceta recomendada');
                potRows.forEach(([k, v]) => K.kv(k, v));
                if ((pot.extraTips || []).filter(Boolean).length) { K.gap(1); K.bullets(pot.extraTips); }
                K.gap(2);
            }

            // ── 6 · Plan de cuidado ──
            const planRows = [['Esta semana', plan.thisWeek], ['Este mes', plan.thisMonth], ['De forma continua', plan.ongoing]].filter(([, a]) => (a || []).filter(Boolean).length);
            const warn = (rep.warnings || []).filter(Boolean);
            if (planRows.length || warn.length) {
                K.section('Plan de cuidado');
                planRows.forEach(([k, a]) => { K.sub(k); K.bullets(a); });
                if (warn.length) K.card({ title: 'Ten en cuenta', blocks: [{ text: warn.map(trim).join('. ') + '.', color: C.txt }], fill: C.tint, accent: C.warn });
            }

            const total = K.footers();
            const base = K.T(id.commonName || 'Planta').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40).replace(/_$/, '') || 'Planta';
            doc.save(`EcoScan_Planta_${base}_${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}.pdf`);
            this.showToast(`✅ PDF descargado (${total} página${total > 1 ? 's' : ''})`, 'success');
        } catch (err) {
            console.error('PDF error:', err);
            this.showToast('Error al generar el PDF: ' + err.message, 'error');
        }
    }

    async openCameraModal() {
        document.getElementById('cameraModal').style.display = 'flex';
        document.getElementById('scanResult')?.classList.add('hidden');
        try {
            const video = document.getElementById('cameraVideo');
            this.videoStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
            video.srcObject = this.videoStream;
        } catch {
            this.showToast('No se pudo acceder a la cámara. Usa "Subir imagen".', 'error');
        }
    }

    capturePhoto() {
        const video  = document.getElementById('cameraVideo');
        const canvas = document.getElementById('cameraCanvas');
        const vw = video.videoWidth || 640, vh = video.videoHeight || 480, ratio = Math.min(1024 / vw, 1024 / vh, 1);
        canvas.width = Math.round(vw * ratio); canvas.height = Math.round(vh * ratio);
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        this.analyzeImageWithAI(canvas.toDataURL('image/jpeg', 0.8));
    }

    handleImageUpload(event) {
        const file = event.target.files[0];
        event.target.value = '';          // así se puede volver a elegir la misma foto
        if (!file) return;
        if (!/^image\//.test(file.type)) { this.showToast('Elige un archivo de imagen (JPG, PNG o WebP).', 'warning'); return; }
        const reader = new FileReader();
        reader.onload = (e) => {
            const img = new Image();
            img.onerror = () => this.showToast('No se pudo leer la imagen. Prueba con otra foto.', 'error');
            img.onload = () => {
                const canvas = document.getElementById('cameraCanvas');
                const MAX = 1024, ratio = Math.min(MAX/img.width, MAX/img.height, 1);
                canvas.width = img.width*ratio; canvas.height = img.height*ratio;
                canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
                this.analyzeImageWithAI(canvas.toDataURL('image/jpeg', 0.8));
            };
            img.src = e.target.result;
        };
        reader.readAsDataURL(file);
    }

    async analyzeImageWithAI(dataUrl) {
        if (this._scanBusy) { this.showToast('Ya hay un análisis en curso, espera a que termine.', 'info'); return; }
        this._scanBusy = true;
        try { return await (this.scanMode === 'plant' ? this.analyzePlantImage(dataUrl) : this._analyzeSoilImage(dataUrl)); }
        finally { this._scanBusy = false; }
    }

    async _analyzeSoilImage(dataUrl) {
        const resultDiv    = document.getElementById('scanResult');
        const analysisText = document.getElementById('scanAnalysis');
        const previewImg   = document.getElementById('capturedPreview');
        const applyBtn     = document.getElementById('applyScanResult');

        // Spinner en modal mientras escanea
        if (previewImg)  { previewImg.src = dataUrl; previewImg.style.display = 'block'; }
        if (applyBtn)      applyBtn.style.display = 'none';
        if (resultDiv)     resultDiv.classList.remove('hidden');
        if (analysisText)  analysisText.innerHTML = `
            <div style="display:flex;align-items:center;gap:.75rem;padding:.5rem 0;">
                <div style="width:36px;height:36px;border-radius:50%;background:var(--accent-light);display:flex;align-items:center;justify-content:center;">
                    <i class="fas fa-spinner fa-spin" style="color:var(--accent-primary);"></i>
                </div>
                <div>
                    <strong style="display:block;font-size:.9rem;">Escaneando imagen con IA...</strong>
                    <span style="font-size:.8rem;color:var(--text-secondary);">Comparando con base de datos + datos satelitales</span>
                </div>
            </div>`;

        try {
            // ── PASO 1: Escaneo visual ─────────────────────────────────────────
            let scanResult = await this._apiPost('/api/scan-image', { image: dataUrl, region: this.userRegion, lat: this.userLat, lng: this.userLng });

            // Imagen inválida: error en el modal para que reintente
            if (!scanResult.isSoil) {
                if (previewImg) previewImg.style.display = 'none';
                if (analysisText) analysisText.innerHTML = `
                    <div style="text-align:center;padding:.75rem 0;">
                        <i class="fas fa-ban" style="font-size:2.5rem;color:var(--error);margin-bottom:.5rem;display:block;"></i>
                        <strong style="color:var(--error);">No se detectó suelo en la imagen</strong>
                        <p style="margin-top:.5rem;font-size:.85rem;color:var(--text-secondary);">${this._esc(scanResult.rejection_reason || 'Apunta directamente al suelo (tierra, arena, arcilla).')}</p>
                    </div>`;
                return;
            }

            // ── PASO 2: Llenar formulario silenciosamente ──────────────────────
            this._fillFormFromScan(scanResult);

            // v3.1: la pregunta de ubicación solo llega aquí si la imagen ya pasó la validación
            const loc = await this._resolveScanLocation(scanResult);
            if (loc === undefined) return;   // cerró el modal durante la pregunta de ubicación
            this.currentData = {
                soilType:    scanResult.soilType    || 'franco',
                ph:          null,   // el pH visual es una estimación de la IA: viaja en "scan" con su procedencia
                phMethod:    '',
                compaction:  scanResult.compaction   || 'media',
                drainage:    scanResult.drainage     || 'moderado',
                erosion:     scanResult.erosion      || 'media',
                fireHistory: scanResult.fireHistory  || 'ninguno',
                goal:        document.getElementById('goal')?.value || 'mejorar suelo',
                region:      loc?.region || '',
                latitude:    loc?.lat ?? '',
                longitude:   loc?.lng ?? '',
            };

            this._lastScanCtx = { scan: scanResult, photo: dataUrl, loc: loc || null };   // para «Agregar mis mediciones»

            // ── PASO 3: Cerrar modal + loading overlay para análisis completo ──
            this.closeCameraModal();
            this.hideResults();
            this.showLoading(true, '🔬 Tipo detectado: ' + (scanResult.soilType || 'N/A') + ' · pH visual (orientativo): ' + (scanResult.estimatedPh || 'N/A') + ' · Generando diagnóstico completo...');

            // ── PASO 4: Análisis completo con /api/analyze ────────────────────
            // un solo análisis de IA: el servidor añade SoilGrids (si hay ubicación) y los perfiles de la base de datos
            const fullAnalysis = await this.analyzeWithAI(this.currentData, scanResult);
            this.currentResults = fullAnalysis;

            // ── PASO 5: Mostrar igual que el formulario + paneles de foto/BD/SG
            this.displayResults(fullAnalysis, { scan: scanResult, photo: dataUrl });

            this.showLoading(false);
            this.showResults();
            this.showToast('✅ Diagnóstico completo generado desde tu fotografía', 'success');

        } catch (err) {
            this.showLoading(false);
            console.error('Scan/analyze error:', err);
            this.showToast(err.message, 'error');
            if (document.getElementById('cameraModal')?.style.display === 'none') {
                this.displayError('Error al generar el diagnóstico: ' + err.message);
                this.showResults();
            } else {
                if (previewImg) previewImg.style.display = 'none';
                if (analysisText) analysisText.innerHTML = this._aiAlert(err.message);
            }
        }
    }

    // ── Rellena formulario silenciosamente con datos del scan ─────────────────
    _fillFormFromScan(r) {
        // El pH de la foto es solo una estimación de la IA: NO se copia al formulario (se enviaría como dato declarado).
        const set = (id, val) => { if (!val) return; const el = document.getElementById(id); if (el) el.value = val; };
        set('soilType', r.soilType); set('compaction', r.compaction);
        set('drainage', r.drainage); set('erosion', r.erosion); set('fireHistory', r.fireHistory);
    }

    applyScanToForm() {
        if (!this.scanResult) { this.showToast('No hay datos de escaneo para aplicar', 'error'); return; }
        const s = this.scanResult;
        const set = (id, val) => { const el = document.getElementById(id); if (el && val) el.value = val; };
        set('soilType', s.soilType); set('phLevel', s.ph); set('phSlider', s.ph);
        set('compaction', s.compaction); set('drainage', s.drainage);
        set('erosion', s.erosion); set('fireHistory', s.fireHistory);
        const phVal = parseFloat(s.ph);
        const slider = document.getElementById('phSlider');
        if (slider) slider.style.accentColor = phVal<6?'#d32f2f':phVal<=7.5?'#2e7d32':'#ff9800';
        this.closeCameraModal();
        this.showToast('✅ Formulario completado con datos del escáner IA', 'success');
    }

    closeCameraModal() {
        this._locResolve?.('cancel');
        document.getElementById('cameraModal').style.display = 'none';
        if (this.videoStream) { this.videoStream.getTracks().forEach(t => t.stop()); this.videoStream = null; }
    }

    showGuidesModal(tab='ph') {
        document.getElementById('guidesModal').style.display = 'flex';
        this.switchGuideTab(tab);
    }

    closeGuidesModal() { document.getElementById('guidesModal').style.display = 'none'; }

    switchGuideTab(tab) {
        document.querySelectorAll('.guide-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.guide-content').forEach(c => c.classList.remove('active'));
        document.querySelector(`[data-guide="${tab}"]`)?.classList.add('active');
        document.getElementById(`${tab}Guide`)?.classList.add('active');
    }

    // ── RECORDATORIOS ────────────────────────────────────────────────────────
    scheduleReminder() {
        if (!('Notification' in window)) { this.showToast('Notificaciones no disponibles', 'error'); return; }
        if (Notification.permission === 'granted') this.setReminder();
        else if (Notification.permission !== 'denied') Notification.requestPermission().then(p => { if (p==='granted') this.setReminder(); });
        else this.showToast('Notificaciones bloqueadas en el navegador', 'error');
    }

    setReminder() {
        new Notification('EcoScan 🌱', { body:'¡Es hora de revisar tu suelo!' });
        this.showToast('✅ Recordatorio activado', 'success');
    }

    // ── FORMULARIO ───────────────────────────────────────────────────────────
    async handleSubmit(e) {
        e.preventDefault();
        const soilType = document.getElementById('soilType').value;
        const phRaw = document.getElementById('phLevel').value.trim();
        const ph = phRaw === '' ? null : parseFloat(phRaw);      // vacío = sin dato (ya no se asume 7)
        const goal = document.getElementById('goal').value;
        if (!soilType || !goal) { this.showToast('Completa los campos requeridos (*)', 'error'); return; }
        if (ph !== null && (isNaN(ph) || ph < 0 || ph > 14)) { this.showToast('El pH debe ser entre 0 y 14', 'error'); return; }

        const carry = this._carryScan || null;                  // viene de «Agregar mis mediciones» tras un escaneo
        this._carryScan = null;
        this.currentData = {
            soilType, ph,
            phMethod: ph === null ? '' : (document.getElementById('phMethod')?.value || 'estimate'),
            fireHistory: document.getElementById('fireHistory').value || 'ninguno',
            compaction:  document.getElementById('compaction').value  || 'media',
            drainage:    document.getElementById('drainage').value    || 'moderado',
            erosion:     document.getElementById('erosion').value     || 'media',
            goal,
            region:    this.userRegion || document.getElementById('userRegion').value || '',
            latitude:  this.userLat   || document.getElementById('userLatitude').value  || '',
            longitude: this.userLng   || document.getElementById('userLongitude').value || '',
            ...this._collectMeasured()
        };

        this.showLoading(true); this.hideResults();
        try {
            const analysis = await this.analyzeWithAI(this.currentData, carry?.scan);
            this.currentResults = analysis;
            this.displayResults(analysis, carry ? { scan: carry.scan, photo: carry.photo } : {});
            this.showResults();
        } catch (err) {
            this.displayError(err.message);
            this.showToast(err.message, 'error');
        } finally {
            this.showLoading(false);
        }
    }

    // Mediciones y pruebas de campo del formulario (solo lo que el usuario escribió)
    _collectMeasured() {
        const v = (id) => String(document.getElementById(id)?.value ?? '').trim();
        const put = (o, k, val) => { if (val !== '') o[k] = val; };
        const m = {}, t = {};
        [['ecValue', 'ec'], ['omValue', 'om'], ['cecValue', 'cec'], ['pValue', 'p'], ['kValue', 'k'], ['nValue', 'n'],
         ['clayLab', 'clay'], ['sandLab', 'sand'], ['siltLab', 'silt']].forEach(([id, k]) => put(m, k, v(id)));
        if (m.ec !== undefined) { m.ecUnit = v('ecUnit') || 'dS/m'; m.ecMethod = v('ecMethod') || 'ece'; }
        if (m.p !== undefined)  m.pMethod = v('pMethod') || 'olsen';
        if (Object.keys(m).length) { m.by = v('measBy') || 'kit'; m.depth = v('measDepth'); }
        [['testVinegar', 'vinegar'], ['testCrust', 'crust'], ['testRibbon', 'ribbon'],
         ['jarClay', 'jarClay'], ['jarSand', 'jarSand'], ['jarSilt', 'jarSilt']].forEach(([id, k]) => put(t, k, v(id)));
        return { measured: m, tests: t, share: !!document.getElementById('shareConsent')?.checked };
    }

    // Botón del reporte: abre el formulario en «Datos medidos» conservando la foto y la ubicación del escaneo
    addMeasurements() {
        const ctx = this._lastScanCtx;
        if (ctx?.scan) {
            this._carryScan = { scan: ctx.scan, photo: ctx.photo };
            this._fillFormFromScan(ctx.scan);
            if (ctx.loc?.lat != null && ctx.loc?.lng != null) {
                this.userLat = ctx.loc.lat; this.userLng = ctx.loc.lng; this.userRegion = ctx.loc.region || '';
                document.getElementById('userLatitude').value  = ctx.loc.lat;
                document.getElementById('userLongitude').value = ctx.loc.lng;
                document.getElementById('userRegion').value    = ctx.loc.region || '';
                const box = document.getElementById('enableLocation'); if (box) box.checked = true;
                const lt = document.getElementById('locationText'); if (lt) lt.textContent = '📍 ' + (ctx.loc.region || `${Number(ctx.loc.lat).toFixed(4)}, ${Number(ctx.loc.lng).toFixed(4)}`);
                document.getElementById('locationDisplay')?.classList.remove('hidden');
            }
        }
        this.toggleManualForm('soil', true);
        const det = document.getElementById('measuredBox');
        if (det) { det.open = true; setTimeout(() => det.scrollIntoView({ behavior: 'smooth', block: 'start' }), 220); }
        this.showToast('Ingresa solo lo que mediste y pulsa «Analizar con IA» para recalcular.', 'info');
    }

    // ── ANÁLISIS IA ──────────────────────────────────────────────────────────
    async analyzeWithAI(data, scan) {
        const ai = await this._apiPost('/api/analyze', { data, scan: scan || undefined });
        return this.formatAIResponse(ai, data);
    }

    formatAIResponse(ai, data) {
        // La respuesta viene de una IA: se garantiza la forma (listas, textos, números) antes de pintarla
        ai = (ai && typeof ai === 'object') ? ai : {};
        const L = (v) => Array.isArray(v) ? v.filter((x) => x != null && x !== '') : (typeof v === 'string' && v.trim() ? [v.trim()] : []);
        const R = (v) => L(v).map((x) => (x && typeof x === 'object') ? x : { name: String(x) });
        const O = (v) => (v && typeof v === 'object' && !Array.isArray(v)) ? v : null;
        const T = (v) => (v == null || typeof v === 'object') ? '' : String(v);
        const ss = O(ai.soilStatus) || {}, re = O(ai.rehabilitation) || {}, pl = O(ai.plants) || {}, cr = O(ai.careRoutine) || {};
        const pe = O(ai.pests), su = O(ai.suitability), wm = O(ai.waterManagement);
        const sc = su ? Number(String(su.score).replace(',', '.').replace(/[^\d.\-]/g, '')) : NaN;
        return {
            phAnalysis:       T(ss.phAnalysis),
            soilHealth:       T(ss.soilHealth),
            mainIssues:       L(ss.mainIssues),
            salinity:         O(ss.salinity),
            nutrients:        O(ss.nutrients),
            pests:            pe ? { ...pe, commonPests: R(pe.commonPests), preventionTips: L(pe.preventionTips) } : null,
            immediateActions: L(re.immediateActions),
            longTermActions:  L(re.longTermActions),
            amendments:       L(re.amendments),
            suitability:      su ? { ...su, level: T(su.level) || 'N/A', score: Number.isFinite(sc) ? Math.max(0, Math.min(100, Math.round(sc))) : 50, message: T(su.message) } : { level: 'N/A', score: 50, message: '' },
            plants:           { reforestation: R(pl.reforestation), agriculture: R(pl.agriculture), coverCrops: R(pl.coverCrops) },
            waterManagement:  wm ? { ...wm, tips: L(wm.tips) } : null,
            careRoutine:      { soilCare: L(cr.soilCare), plantCare: L(cr.plantCare), schedule: T(cr.schedule) },
            soilGrids: O(ai.soilGridsData),
            climate: O(ai.climate),
            precision: O(ai.precision),
            dbMatches: L(ai.databaseMatches).filter((m) => m && typeof m === 'object'),
            aiGenerated: true
        };
    }

    // ── RENDERIZADO DE RESULTADOS (informe de suelo) ──────────────────────────
    displayResults(a, ctx = {}) {
        const box = document.getElementById('resultsContent');
        if (!box) return;
        this._setResultsMode('soil');
        this._rerender = () => this.displayResults(a, ctx);
        this._watchViewport();
        const mobile = this._isMobile(), wide = this._isWide();
        this._rpNav = [];
        const e    = (s) => this._esc(s);
        const cap  = (s) => { s = String(s ?? ''); return s.charAt(0).toUpperCase() + s.slice(1); };
        const scan = ctx.scan || null, photo = ctx.photo || null, d = this.currentData || {};
        const prec = a.precision || null, P = (prec && prec.properties) || {};
        const chipP = (t, code) => { if (!t) return ''; const k = PROV[code] ? code : 'none'; return `<span class="rp-chip">${e(t)} <i class="rp-dot" title="${e(PROV_PLAIN[k])}">${PROV[k][0]}</i></span>`; };
        const chip  = (t) => t ? `<span class="rp-chip">${e(t)}</span>` : '';
        const srcOf = (k) => (scan && (!d[k] || scan[k] === d[k] || scan[k] === undefined)) ? 'ai' : 'declared';
        const sg = a.soilGrids || null, dbm = a.dbMatches || [];
        const sui = a.suitability || { level: 'N/A', score: 0, message: '' };
        const score = Math.round(Math.max(0, Math.min(100, Number(sui.score) || 0)));
        const sc    = { 'Óptimo': '#6ab04c', 'Aceptable': '#ff9800', 'Requiere rehabilitación': '#f44336', 'Crítico': '#b71c1c' }[sui.level] || '#8bc34a';
        const salC  = { baja: '#6ab04c', media: '#ff9800', alta: '#f44336', 'muy alta': '#b71c1c' };
        const riskC = { bajo: '#6ab04c', medio: '#ff9800', alto: '#f44336', 'crítico': '#b71c1c' };
        const goals    = { reforestacion: 'reforestación ecológica', agricultura: 'agricultura para consumo', ambos: 'reforestación y agricultura' };
        const typeName = cap(String(scan?.soilType || d.soilType || 'N/A').replace(/_/g, ' '));
        const ph       = P.ph && P.ph.value != null ? P.ph.value : 'sin dato';
        const sal = a.salinity, nut = a.nutrients, pe = a.pests, wm = a.waterManagement, cr = a.careRoutine, pl = a.plants || {};
        const dq  = prec && prec.dataQuality;
        const dqC = dq ? ({ alta: '#6ab04c', media: '#ff9800', baja: '#f44336' }[dq.level] || '#8bc34a') : '#8bc34a';
        const rk  = pe ? String(pe.riskLevel || 'bajo').toLowerCase() : '';
        const fixDot = (s) => String(s).replace(/[.\s]+$/, '');

        // ── Encabezado ──
        const chips = [
            chipP(P.ph && P.ph.value != null ? 'pH ' + P.ph.value + ' ±' + (Math.round(1.645 * P.ph.sd * 10) / 10) : 'pH sin dato', P.ph ? P.ph.primary : 'none'),
            prec && prec.texture ? chipP('Textura ' + prec.texture.es, prec.texture.primary) : '',
            chipP('Compactación ' + (d.compaction || scan?.compaction || 'N/A'), srcOf('compaction')),
            chipP('Drenaje ' + (d.drainage || scan?.drainage || 'N/A'), srcOf('drainage')),
            chipP('Erosión ' + (d.erosion || scan?.erosion || 'N/A'), srcOf('erosion')),
            scan?.humidity ? chip('Humedad ' + scan.humidity) : '',
            scan?.structureQuality ? chip('Estructura ' + scan.structureQuality) : '',
            d.fireHistory && d.fireHistory !== 'ninguno' ? chip('Incendio ' + d.fireHistory) : ''
        ].join('');
        const look = scan && (scan.color || scan.texture) ? [scan.color, scan.texture].filter(Boolean).join('. ') : '';
        const hero = `<header class="rp-hero">
            <div class="rp-media">${photo ? `<img src="${e(photo)}" alt="Foto del suelo">` : '<i class="fas fa-mountain"></i>'}</div>
            <div class="rp-id">
                <div class="rp-id-top">
                    <h4 class="rp-name">${e(typeName)}</h4>
                    <p class="rp-sub">${e(d.region || (scan ? 'Análisis por fotografía' : 'Análisis por formulario'))}</p>
                    <p class="rp-meta">${scan?.confidence != null ? 'Confianza de la IA: <strong>' + e(scan.confidence) + '%</strong> · ' : ''}Objetivo: ${e(goals[d.goal] || d.goal || 'mejorar el suelo')}${dq ? ' · Calidad de los datos: <strong>' + e(dq.level) + ' (' + e(dq.score) + '/100)</strong>' : ''}</p>
                </div>
                <div class="rp-chips">${chips}</div>
            </div>
            ${mobile ? '' : `<div class="rp-score">${this._rpGauge(score, sc)}<span class="rp-level" style="--c:${sc}">${e(sui.level)}</span></div>`}
        </header>`;
        const lead = [sui.message, look].filter(Boolean).map((t) => `<p class="rp-lead">${e(t)}</p>`).join('');

        // ── Resumen para celular ──
        const stat = (label, val, code) => `<div class="rp-stat"><small>${label}</small><b>${val}${code ? ` <i class="rp-dot" title="${e(PROV_PLAIN[code] || '')}">${(PROV[code] || PROV.none)[0]}</i>` : ''}</b></div>`;
        const glance = mobile ? `<section class="rp-glance" aria-label="Resumen">
            <div class="rp-glance-top">${this._rpGauge(score, sc, 68)}<div><span class="rp-level" style="--c:${sc}">${e(sui.level)}</span>${sui.message ? `<p>${e(sui.message)}</p>` : ''}</div></div>
            <div class="rp-stats">
                ${stat('pH', e(ph), P.ph ? P.ph.primary : 'none')}
                ${stat('Salinidad', e(sal ? cap(sal.level || 'N/A') : 'Sin dato'))}
                ${stat('Calidad de datos', dq ? e(cap(dq.level)) + ' · ' + e(dq.score) : 'Sin dato')}
                ${stat('Riesgo de plagas', pe ? e(cap(rk)) : 'Sin dato')}
            </div>
            ${this._rpFirst('⚡ Primero haz esto', a.immediateActions, 'rp-plan', 'Ver plan completo')}
        </section>` : '';

        const secs = [];

        // 1 — Estado del suelo
        {
            const rows = this._rpRows([['Química', a.phAnalysis], ['Salud general', a.soilHealth]]);
            const issues = this._rpGroup('Problemas detectados', a.mainIssues);
            const refs = dbm.length ? `<p class="rp-small rp-muted">📚 Perfiles de referencia: ${e(dbm.map((m) => m.name + ' (' + m.match_score + '/100)').join(' · '))}</p>` : '';
            const body = rows + issues + refs;
            const bar = mobile ? '' : `<div class="rp-line"><span class="rp-pill" style="--c:${sc}">${e(sui.level)}</span><div class="rp-bar" style="--c:${sc}"><span><i style="width:${score}%"></i></span><small>${score}/100</small></div></div>`;
            if (body || bar) secs.push(this._rpSec('state', 'fa-chart-pie', 'Estado del suelo', { nav: 'Estado', prev: `${sui.level} · ${score}/100`, open: true }, bar + body));
        }

        // 2 — Salinidad y nutrientes (cifras solo si hay medición)
        if (sal || nut) {
            const nIc = (v) => v === 'deficiente' ? '🔴' : v === 'exceso' ? '🟠' : v === 'sin dato' ? '⚪' : '🟢';
            const dt  = (nut && nut.detail) || {};
            const nutVal = (key) => {
                const lvl = nut[key], x = dt[key];
                if (!lvl || lvl === 'sin dato') return 'Sin medición' + (x && x.potential ? ` · potencial según la materia orgánica: ${x.potential}` : '');
                return lvl + (x && x.value != null ? ` · ${x.value} ${x.unit}${x.class ? ' (' + x.class + ')' : ''}` : '');
            };
            const nutCell = (key, name) => [nut[key] ? nIc(nut[key]) : '⚪', `${name} ${provChip((dt[key] && dt[key].source) || 'none')}`, nut[key] ? nutVal(key) : 'Sin medición'];
            const omD = dt.organicMatter;
            const salBlock = sal ? `<div class="rp-line"><span class="rp-pill" style="--c:${salC[String(sal.level || 'baja').toLowerCase()] || '#666'}">Salinidad ${e(String(sal.level || 'N/A').toUpperCase())}</span>
                    ${sal.estimatedDsM != null ? `<span class="rp-muted">${e(sal.estimatedDsM)} dS/m${sal.interval ? ` <small>(90 %: ${e(sal.interval[0])}–${e(sal.interval[1])})</small>` : ''}${sal.classLabel ? ' · ' + e(sal.classLabel) : ''}</span>` : '<span class="rp-muted rp-small">sin cifra en dS/m · no hay medición de conductividad</span>'}
                    ${mobile ? '' : provChip(sal.source || 'ai')}</div>
                ${mobile ? `<div>${provChip(sal.source || 'ai')}</div>` : ''}
                ${sal.basis ? `<p class="rp-small rp-muted">${e(sal.basis)}</p>` : ''}
                ${sal.impact ? `<p class="rp-muted">${e(sal.impact)}</p>` : ''}
                ${sal.recommendation ? `<div class="rp-callout"><strong>Recomendación:</strong> ${e(sal.recommendation)}</div>` : ''}` : '';
            const nutBlock = nut ? `${this._rpCells([nutCell('nitrogen', 'Nitrógeno'), nutCell('phosphorus', 'Fósforo'), nutCell('potassium', 'Potasio'), ['🟤', `Materia orgánica ${provChip(omD ? omD.source : 'ai')}`, nut.organicMatter ? nut.organicMatter + (omD && omD.value != null ? ` · ${omD.value} % (${omD.class})` : '') : '']])}${nut.analysis ? `<p class="rp-muted">${e(nut.analysis)}</p>` : ''}` : '';
            const prev = [sal ? 'Salinidad ' + (sal.level || 'N/A') : '', nut && nut.nitrogen && nut.nitrogen !== 'sin dato' ? 'N ' + nut.nitrogen : ''].filter(Boolean).join(' · ');
            secs.push(this._rpSec('nutri', 'fa-flask', 'Salinidad y nutrientes', { nav: 'Nutrientes', prev }, salBlock + nutBlock));
        }

        // 3 — Plan de rehabilitación
        const planGroups = [['⚡ Acciones inmediatas', a.immediateActions], ['📅 Largo plazo', a.longTermActions], ['🌾 Enmiendas', a.amendments]];
        if (planGroups.some((g) => (g[1] || []).length)) {
            secs.push(this._rpSec('plan', 'fa-tools', 'Plan de rehabilitación',
                { nav: 'Plan', prev: (a.immediateActions || []).length ? (a.immediateActions || []).length + ' acciones inmediatas' : '', open: !mobile },
                planGroups.map((g) => this._rpGroup(g[0], g[1])).join('')));
        }

        // 4 — Plagas y enfermedades
        if (pe) {
            const rc = riskC[rk] || '#666';
            const n = (pe.commonPests || []).length;
            secs.push(this._rpSec('pests', 'fa-bug', 'Plagas y enfermedades',
                { nav: 'Plagas', prev: `Riesgo ${rk}${n ? ' · ' + n + ' amenaza' + (n > 1 ? 's' : '') : ''}` },
                `<div class="rp-line"><span class="rp-muted">Riesgo general</span><span class="rp-pill" style="--c:${rc}">${e(rk.toUpperCase())}</span></div>
                 ${(pe.commonPests || []).map((p) => `<div class="rp-issue" style="--c:${rc}">
                    <div class="rp-issue-h"><strong>${e(p.name)}</strong>${p.type ? `<span class="rp-tagsm">${e(p.type)}</span>` : ''}</div>
                    ${p.description ? `<p>${e(p.description)}</p>` : ''}
                    ${p.control ? `<p class="fix"><i class="fas fa-shield-alt"></i>${e(p.control)}</p>` : ''}
                 </div>`).join('')}
                 ${this._rpGroup('Prevención', pe.preventionTips)}`));
        }

        // 5 — Agua y rutina de cuidado
        if (wm || (cr && ((cr.soilCare || []).length || (cr.plantCare || []).length || cr.schedule))) {
            secs.push(this._rpSec('water', 'fa-water', 'Agua y rutina de cuidado',
                { nav: 'Agua', prev: wm && wm.irrigationType ? wm.irrigationType : '' },
                `${wm ? this._rpCells([['💧', 'Tipo de riego', wm.irrigationType], ['⏱️', 'Frecuencia', wm.frequency]]) + this._rpList(wm.tips) : ''}
                 ${cr ? this._rpGroup('🌍 Cuidado del suelo', cr.soilCare) + this._rpGroup('🌿 Cuidado de las plantas', cr.plantCare) : ''}
                 ${cr && cr.schedule ? `<div class="rp-callout"><strong>📆 Calendario:</strong> ${e(cr.schedule)}</div>` : ''}`));
        }

        // 8 — Plantas recomendadas
        {
            const plantCards = (arr, extra) => (arr || []).length ? `<div class="rp-plants">${arr.map((p) => `<div class="rp-plant"><strong>${e(p.name)}</strong>${(p.reason || p.benefit) ? `<p>${e(p.reason || p.benefit)}</p>` : ''}${extra(p) ? `<small>${e(extra(p))}</small>` : ''}</div>`).join('')}</div>` : '';
            const groups = [
                ['🌳 Reforestación / nativas', plantCards(pl.reforestation, (p) => [p.nativeRegion, p.care, p.waterNeeds ? 'agua ' + p.waterNeeds : ''].filter(Boolean).join(' · '))],
                ['🌽 Agricultura / cultivos', plantCards(pl.agriculture, (p) => [p.care, p.season ? 'siembra: ' + p.season : '', p.yield ? 'rendimiento: ' + p.yield : ''].filter(Boolean).join(' · '))],
                ['🌱 Cultivos de cobertura', plantCards(pl.coverCrops, (p) => p.howToUse || '')]
            ].filter((g) => g[1]);
            if (groups.length) {
                const total = (pl.reforestation || []).length + (pl.agriculture || []).length + (pl.coverCrops || []).length;
                secs.push(this._rpSec('plants', 'fa-leaf', 'Plantas recomendadas', { nav: 'Plantas', prev: total + ' especies para tu terreno', wide: true },
                    groups.map((g) => `<div class="rp-group"><div class="rp-subl">${g[0]}</div>${g[1]}</div>`).join('')));
            }
        }

        // 6 — Datos modelados por satélite y clima del sitio (solo con ubicación)
        const cl = a.climate || null;
        if (sg || cl) {
            const u  = (v, s) => (v != null ? v + s : '');
            const rg = (k, s = '') => (sg && sg.range90 && sg.range90[k] ? ` (${sg.range90[k][0]}–${sg.range90[k][1]}${s})` : '');
            const num = (v) => (typeof v === 'number' && Number.isFinite(v));
            const supp = (sg && sg.supplement) || {};
            const soilPart = sg ? `${this._rpCells([['🧪', 'pH modelado', sg.ph_real != null ? sg.ph_real + rg('ph_real') : ''], ['🧱', 'Arcilla', sg.clay_pct != null ? sg.clay_pct + ' %' + rg('clay_pct') : ''], ['🏖️', 'Arena', u(sg.sand_pct, ' %')], ['🌫️', 'Limo', u(sg.silt_pct, ' %')], ['🌿', 'Carbono orgánico', u(sg.organic_carbon_pct, ' %')], ['⚡', 'CIC', u(sg.cec_cmolkg, ' cmol/kg')], ['⚖️', 'Densidad aparente', u(sg.bulk_density_g_cm3, ' g/cm³')], ['🧬', 'Nitrógeno total', u(sg.nitrogen_g_kg, ' g/kg')], ['📐', 'Textura modelada', sg.texture_class || '']])}
                 <p class="rp-small rp-muted">Fuente: ${e(sg.source || 'ISRIC SoilGrids (modelo, 250 m)')}; capa ${e(sg.depth || '0-5 cm')}. Son <strong>predicciones</strong> de un modelo de aprendizaje automático, no mediciones de tu terreno${sg.range90 ? '; entre paréntesis, el rango probable (90 %)' : ''}. Pesan menos que tus mediciones y que un perfil medido muy cercano.${(supp.cec_cmolkg || supp.nitrogen_g_kg) ? ' La CIC y el nitrógeno vienen de ' + e(supp.cec_cmolkg || supp.nitrogen_g_kg) + '.' : ''}</p>` : '';
            const koppen = cl && cl.koppen ? cl.koppen + (cl.koppenName ? ' · ' + cl.koppenName : '') : '';
            const climPart = cl ? `<div class="rp-group"><div class="rp-subl">🌦️ Clima del sitio</div>${this._rpCells([['⛰️', 'Altitud', num(cl.elevation_m) ? cl.elevation_m + ' m' : ''], ['🗺️', 'Clima (Köppen)', koppen], ['🌡️', 'Temperatura media anual', num(cl.mat_c) ? cl.mat_c + ' °C' + (num(cl.t_cold_c) && num(cl.t_hot_c) ? ` (de ${cl.t_cold_c} a ${cl.t_hot_c} °C entre meses)` : '') : ''], ['❄️', 'Heladas', cl.frost ? cl.frost + (num(cl.tmin_cold_c) ? ` (mínima media del mes más frío: ${cl.tmin_cold_c} °C)` : '') : ''], ['🌧️', 'Lluvia anual', num(cl.map_mm) ? cl.map_mm + ' mm' + (cl.wettest ? ` · mes más lluvioso: ${cl.wettest[0]} (${cl.wettest[1]} mm)` : '') : ''], ['☔', 'Temporada de lluvias', cl.wet_months || ''], ['🏜️', 'Meses secos', cl.dry_months || ''], ['☀️', 'Radiación solar', num(cl.sw_kwh_m2_day) ? cl.sw_kwh_m2_day + ' kWh/m²/día' : ''], ['💦', 'Humedad relativa', num(cl.rh_pct) ? cl.rh_pct + ' %' : '']])}
                 <p class="rp-small rp-muted">Fuente: ${e(cl.source || 'NASA POWER')}. Son normales de largo plazo de la zona${cl.tempAdjusted ? ', con la temperatura ajustada a la altitud de tu punto' : ''}; la IA las usó para elegir plantas y calendario, en lugar de suponer tu clima.</p></div>` : '';
            const prev = sg ? [sg.ph_real != null ? 'pH ' + sg.ph_real : '', sg.clay_pct != null ? 'arcilla ' + sg.clay_pct + ' %' : ''].filter(Boolean).join(' · ')
                            : [cl.koppen ? 'Köppen ' + cl.koppen : '', num(cl.elevation_m) ? cl.elevation_m + ' m' : ''].filter(Boolean).join(' · ');
            secs.push(this._rpSec('sat', 'fa-globe', 'Datos modelados por satélite',
                { nav: 'Satélite', tag: provChip('modeled'), wide: true, prev },
                soilPart + climPart));
        }

        // 7 — Perfil medido más cercano
        if (prec) {
            const nr = prec.nearest || {}, prv = prec.providers || {};
            const stTxt = { ok: 'consultado', sin_resultados: 'sin resultados', no_disponible: 'no disponible ahora', omitido: 'no hizo falta' };
            const stLine = Object.keys(prv).length ? `<p class="rp-small rp-muted">Fuentes consultadas: perfiles locales (${e(stTxt[prv.local] || '—')}) · WoSIS de ISRIC (${e(stTxt[prv.wosis] || '—')}).</p>` : '';
            const PL = { ph: ['🧪', 'pH', ''], ec: ['⚡', 'CE', ' dS/m'], om: ['🟤', 'Materia orgánica', ' %'], clay: ['🧱', 'Arcilla', ' %'], sand: ['🏖️', 'Arena', ' %'], silt: ['🌫️', 'Limo', ' %'], cec: ['🔋', 'CIC', ' cmol/kg'], p: ['🧬', 'Fósforo', ' mg/kg'], k: ['🌿', 'Potasio', ' cmol/kg'], n: ['🧫', 'N inorgánico', ' mg/kg'] };
            let body, prev;
            if (!Object.keys(prv).length) {
                prev = 'Activa tu ubicación para buscarlo';
                body = '<p class="rp-muted">Activa tu ubicación (en el formulario o al escanear) para buscar el perfil de suelo <strong>medido</strong> más cercano a tu terreno.</p>';
            } else if (nr.found) {
                prev = `a ${nr.km} km · ${nr.source || ''}`;
                const vals = Object.entries(nr.values || {}).filter(([k]) => PL[k]).map(([k, v]) => [PL[k][0], PL[k][1], v + PL[k][2]]);
                const cmp  = (nr.compare || []).filter((c) => PL[c.prop]).map((c) => `<li><strong>${PL[c.prop][1]}</strong>: perfil ${e(c.nearest)}${PL[c.prop][2]} · tu resultado ${e(c.yours)}${PL[c.prop][2]} ${provChip(c.src)}</li>`).join('');
                body = `<div class="np-head"><span class="np-dist">📍 a ${e(nr.km)} km</span><span>${e(nr.source)}${nr.code ? ' · ' + e(nr.code) : ''}${nr.date ? ' · ' + e(String(nr.date).slice(0, 4)) : ''}${nr.depth ? ' · ' + e(nr.depth) : ''}${nr.method ? ' · ' + e(nr.method) : ''}</span></div>
                    ${this._rpCells(vals)}
                    ${cmp ? `<ul class="np-cmp">${cmp}</ul>` : ''}
                    ${nr.note ? `<p class="rp-small rp-muted">${e(nr.note)}</p>` : ''}`;
            } else {
                prev = `Ninguno a menos de ${nr.searchedKm || 30} km`;
                body = `<p class="rp-muted">No se encontró ningún perfil medido a menos de ${e(nr.searchedKm || 30)} km de tu ubicación. Los valores del reporte salen de tus datos, del modelo satelital y de la foto, según se indica en cada etiqueta.</p>`;
            }
            secs.push(this._rpSec('nearest', 'fa-location-crosshairs', 'Perfil medido más cercano', { nav: 'Perfil cercano', tag: provChip('nearby'), wide: true, prev }, body + stLine));
        }

        // 9 — Calidad y procedencia de los datos
        if (dq) {
            const unit = (p) => (p.unit ? ' ' + p.unit : '');
            const srcList = (p) => {
                const li = (s, used) => `<li${used ? '' : ' class="pr-unused"'}>${provChip(s.src)} <strong>${e(s.value)}${e(unit(p))}</strong> <span>±${e(s.sd)}</span> — ${e(s.note || s.label)}${used && s.weight != null && (p.sources || []).length > 1 ? ` <em>(peso ${e(s.weight)} %)</em>` : ''}</li>`;
                const used  = (p.sources || []).map((s) => li(s, true)).join('');
                const other = (p.contrast || []).map((s) => li(s, false)).join('');
                if (!used && !other) return '<p class="pr-basis">Sin medición. Ingresa el dato en «Datos medidos» y recalcula para que aparezca aquí con su procedencia.</p>';
                return `<ul>${used}${other ? `<li class="pr-sep">No usados en el cálculo (de menor nivel; solo sirven para contrastar):</li>${other}` : ''}</ul>${p.basis ? `<p class="pr-basis">Clase según: ${e(p.basis)}</p>` : ''}`;
            };
            const row = (name, valHtml, code, cls, conf, more) => `<details class="prov-row"><summary><span class="pr-name">${name}</span><span class="pr-val">${valHtml}</span><span class="pr-src">${provChip(code)}</span><span class="pr-cls">${cls ? e(cls) : ''}${conf && conf !== 'sin dato' ? `<small>confianza ${e(conf)}</small>` : ''}</span></summary><div class="pr-more">${more}</div></details>`;
            const prow = (key, name) => {
                const p = P[key];
                if (!p) return '';
                if (p.value == null) return row(name, '<span class="pr-none">Sin medición</span>', 'none', '', '', srcList(p));
                const rg = p.low != null && p.high != null && p.low !== p.high ? `<small>90 %: ${e(p.low)}–${e(p.high)}</small>` : '';
                return row(name, `<strong>${e(p.value)}${e(unit(p))}</strong>${rg}`, p.primary, p.class, p.confidence, srcList(p));
            };
            const tx = prec.texture;
            const texRow = tx
                ? row('Textura', `<strong>${e(tx.es)}</strong><small>arcilla ${e(tx.clay)} % · arena ${e(tx.sand)} % · limo ${e(tx.silt)} %</small>`, tx.primary, '', tx.confidence, srcList(P.clay || {}) + '<p class="pr-basis">Arena y limo siguen las mismas fuentes que la arcilla. Clase según el triángulo textural USDA.</p>')
                : row('Textura', '<span class="pr-none">Sin dato suficiente</span>', 'none', '', '', '<p class="pr-basis">Haz la prueba del frasco o de la cinta, o ingresa un análisis de laboratorio.</p>');
            const sevIc = { revisar: 'fa-circle-exclamation', aviso: 'fa-triangle-exclamation', info: 'fa-circle-info' };
            const pct = Math.max(0, Math.min(100, Number(dq.score) || 0));
            secs.push(this._rpSec('quality', 'fa-shield-halved', 'Calidad y procedencia de los datos',
                { nav: 'Calidad', prev: `Calidad ${dq.level} · ${dq.score}/100`, wide: true },
                `<div class="rp-line"><span class="rp-pill" style="--c:${dqC}">Calidad ${e(String(dq.level).toUpperCase())}</span><div class="rp-bar" style="--c:${dqC}"><span><i style="width:${pct}%"></i></span><small>${e(dq.score)}/100</small></div></div>
                 <p class="rp-small rp-muted">${e(dq.message)} Toca una fila para ver de dónde sale cada dato.</p>
                 <div class="prov-table">
                    <div class="prov-head"><span>Propiedad</span><span>Valor</span><span>Procedencia</span><span>Clase</span></div>
                    ${prow('ph', 'pH (en agua)')}${prow('ec', 'Conductividad (salinidad)')}${prow('om', 'Materia orgánica')}${texRow}${prow('cec', 'CIC')}${prow('n', 'Nitrógeno inorgánico')}${prow('p', 'Fósforo disponible')}${prow('k', 'Potasio')}
                 </div>
                 ${(prec.conflicts || []).length ? `<div class="prov-alerts">${prec.conflicts.map((c) => `<p class="prov-alert prov-alert-${e(c.severity)}"><i class="fas ${sevIc[c.severity] || 'fa-circle-info'}"></i> ${e(c.msg)}</p>`).join('')}</div>` : ''}
                 ${(prec.rejected || []).length ? `<p class="rp-small rp-muted">⚠️ ${e(prec.rejected.join(' · '))}</p>` : ''}
                 ${prec.calibration ? `<p class="rp-small rp-muted">🎯 Calibración: al estimar el pH solo por foto, la IA se desvió en promedio ±${e(prec.calibration.mae)} unidades en ${e(prec.calibration.n)} muestras medidas por usuarios.</p>` : ''}
                 ${prec.shared ? `<p class="rp-small rp-muted">🙌 Gracias: tu medición se guardó de forma anónima para mejorar el perfil medido cercano de otros usuarios.</p>` : ''}
                 ${(dq.howToImprove || []).length ? `<div class="prov-improve"><strong><i class="fas fa-arrow-trend-up"></i> Cómo subir la precisión</strong><ul>${dq.howToImprove.map((t) => `<li>${e(t)}</li>`).join('')}</ul><button type="button" class="prov-cta" data-act="add-measures"><i class="fas fa-vial"></i> Agregar mis mediciones y recalcular</button></div>` : ''}
                 <details class="prov-legend"><summary>¿Qué significan las etiquetas?</summary>
                    <p>${provChip('lab')} ${provChip('field')} medición directa de tu muestra (la de mayor peso). ${provChip('test')} indicio de campo. ${provChip('nearby')} suelo medido cerca, pero no en tu punto. ${provChip('modeled')} predicción de un modelo a ${(sg && sg.res_m) || 250} m, <strong>no una medición</strong>. ${provChip('declared')} lo que indicaste sin instrumento. ${provChip('ai')} lo que la IA infiere de la foto: orientativo. ${provChip('none')} no hay dato y no se inventa.</p>
                 </details>`));
        }

        // 10 — EcoChat (mismo componente que en plantas)
        secs.push(this._chatSection('soil'));

        // Cifras clave (solo laptop y tablet)
        const kpiPh = P.ph && P.ph.value != null ? [String(P.ph.value), (P.ph.class ? e(cap(P.ph.class)) + ' ' : '') + `<i class="rp-dot" title="${e(PROV_PLAIN[P.ph.primary] || '')}">${(PROV[P.ph.primary] || PROV.none)[0]}</i>`] : ['Sin dato', 'sin medición'];
        const omP = P.om && P.om.value != null ? P.om : null;
        const kpis = this._rpKpis([
            ['🧪', 'pH', kpiPh[0], kpiPh[1]],
            ['🧂', 'Salinidad', sal ? cap(sal.level || 'N/A') : 'Sin dato', sal ? (sal.estimatedDsM != null ? e(sal.estimatedDsM) + ' dS/m' : 'sin cifra en dS/m') : '', sal ? (salC[String(sal.level || '').toLowerCase()] || '') : ''],
            ['📐', 'Textura', prec && prec.texture ? cap(prec.texture.es) : 'Sin dato', prec && prec.texture ? 'arcilla ' + e(prec.texture.clay) + ' % · arena ' + e(prec.texture.sand) + ' %' : 'sin dato suficiente'],
            ['🟤', 'Mat. orgánica', omP ? omP.value + ' %' : (nut && nut.organicMatter ? cap(nut.organicMatter) : 'Sin dato'), omP && omP.class ? e(cap(omP.class)) : (nut && nut.organicMatter ? 'estimada' : '')],
            ['🛡️', 'Calidad de datos', dq ? cap(dq.level) : 'Sin dato', dq ? e(dq.score) + '/100' : '', dq ? dqC : ''],
            ['🐛', 'Riesgo plagas', pe ? cap(rk) : 'Sin dato', pe ? ((pe.commonPests || []).length ? (pe.commonPests || []).length + ' amenaza' + ((pe.commonPests || []).length > 1 ? 's' : '') : '') : '', pe ? (riskC[rk] || '') : '']
        ]);
        const layout = this._rpLayout(secs), index = this._rpIndex();
        this._rpNav = null;
        box.innerHTML = `<article class="rp rp--soil${wide ? ' rp--wide' : ''}">${hero}${mobile ? glance : lead}${kpis}${index}${layout}</article>`;
        if (wide) this._rpRebalance(box);
        box.querySelectorAll('[data-act="add-measures"]').forEach((b) => b.addEventListener('click', () => this.addMeasurements()));
        this._bindReportActions(box);
        const pcx = (k) => { const p = P[k]; return p && p.value != null ? `${p.label} ${p.value}${p.unit ? ' ' + p.unit : ''} ±${p.sd} (${PROV_PLAIN[p.primary]}, confianza ${p.confidence}${p.class ? ', ' + p.class : ''})` : `${(p && p.label) || k}: sin medición`; };
        const provCtx = prec ? ` DATOS CON PROCEDENCIA: ${['ph', 'ec', 'om', 'cec', 'n', 'p', 'k'].map(pcx).join('; ')}${prec.texture ? `; textura ${prec.texture.es} (${PROV_PLAIN[prec.texture.primary]})` : ''}. Calidad de los datos ${prec.dataQuality.score}/100 (${prec.dataQuality.level}).${(prec.conflicts || []).length ? ' Contradicciones: ' + prec.conflicts.slice(0, 2).map((c) => c.msg).join(' | ') : ''}${prec.nearest && prec.nearest.found ? ` Perfil medido más cercano a ${prec.nearest.km} km (${prec.nearest.source}).` : ''}` : '';
        this.soilCtx = `Suelo ${typeName}, pH ${ph}, compactación ${scan?.compaction || d.compaction || 'N/A'}, drenaje ${scan?.drainage || d.drainage || 'N/A'}, erosión ${scan?.erosion || d.erosion || 'N/A'}. Aptitud: ${sui.level} (${score}/100). Problemas: ${(a.mainIssues || []).join('; ') || 'ninguno'}. Región: ${d.region || 'no indicada'}.${provCtx}`;
        this._bindChat();
    }

    // ── EcoChat: componente compartido por los informes de suelo y de planta ──
    _chatSection(kind) {
        const plant = kind === 'plant';
        return this._rpSec('chat', 'fa-comments', 'Habla con EcoChat',
            { nav: 'EcoChat', prev: 'Pregúntale a la IA sobre ' + (plant ? 'tu planta' : 'tu diagnóstico'), wide: true },
            `<p class="rp-small rp-muted">${plant ? 'Tu asistente de IA para dudas sobre esta planta, sus cuidados, plagas o su maceta' : 'Tu asistente de IA para dudas sobre este diagnóstico, plagas, plantas o manejo del suelo'}</p>
             <div id="chatMessages" class="rp-chat-msgs" aria-live="polite"><div class="rp-msg">¡Hola! Soy EcoChat 🌱 Pregúntame lo que quieras sobre ${plant ? 'tu planta' : 'tu diagnóstico'}.</div></div>
             <div class="rp-chat-form">
                <input id="chatInput" type="text" maxlength="600" autocomplete="off" aria-label="Tu pregunta para EcoChat" placeholder="${plant ? 'Ej: ¿Cada cuánto debo regar mi planta?' : 'Ej: ¿Cómo controlo la salinidad en mi suelo?'}">
                <button id="sendChatBtn" type="button" aria-label="Enviar pregunta"><i class="fas fa-paper-plane"></i></button>
             </div>`);
    }

    _bindChat() {
        document.getElementById('sendChatBtn')?.addEventListener('click', () => this.sendChatMessage());
        document.getElementById('chatInput')?.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); this.sendChatMessage(); } });
    }

    // Respuestas de EcoChat ordenadas: una línea por idea, viñetas y negritas (el texto se escapa primero)
    _mdLite(t) {
        return this._esc(t).split(/\n+/).map((l) => l.trim()).filter(Boolean)
            .map((l) => l.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/^[-*•]\s+/, '• ')).join('<br>');
    }

    // ── CHAT ─────────────────────────────────────────────────────────────────
    async sendChatMessage() {
        const input = document.getElementById('chatInput');
        const msgs  = document.getElementById('chatMessages');
        if (!input || !msgs || this._chatBusy) return;
        const q = input.value.trim();
        if (!q) return;
        input.value = '';
        const add = (html, cls = '') => { const m = document.createElement('div'); m.className = 'rp-msg' + (cls ? ' ' + cls : ''); m.innerHTML = html; msgs.appendChild(m); msgs.scrollTop = msgs.scrollHeight; return m; };
        add(this._esc(q), 'me');
        const typing = add('<i class="fas fa-spinner fa-spin"></i> Pensando...');
        this._chatBusy = true;
        try {
            const d = await this._apiPost('/api/chat', { question: q, context: this._chatContext() });
            typing.remove();
            add(this._mdLite(d.answer || 'Sin respuesta.'));
        } catch (err) {
            typing.remove();
            add(this._esc(err.message || CONN_MSG), 'err');
        } finally {
            this._chatBusy = false;
        }
    }

    // ── UI HELPERS ───────────────────────────────────────────────────────────
    displayError(msg) {
        const c = document.getElementById('resultsContent');
        if (c) {
            c.innerHTML = `<div class="rp-error"><i class="fas fa-exclamation-triangle"></i><h4>Error</h4><p>${this._esc(msg)}</p><button type="button" id="retryBtn">Reintentar</button></div>`;
            document.getElementById('retryBtn')?.addEventListener('click', () => location.reload());
        }
        this.showResults();
    }

    showToast(msg, type = 'info') {
        document.querySelectorAll('.eco-toast').forEach((t) => t.remove());
        const t = document.createElement('div');
        t.className = 'eco-toast eco-toast--' + type;
        t.setAttribute('role', 'status');
        const icon = { success: 'fa-check-circle', error: 'fa-exclamation-circle', warning: 'fa-exclamation-triangle', info: 'fa-info-circle' }[type] || 'fa-info-circle';
        const i = document.createElement('i'); i.className = 'fas ' + icon;
        const s = document.createElement('span'); s.textContent = String(msg ?? '');   // textContent: un mensaje nunca puede inyectar HTML
        t.append(i, s);
        document.body.appendChild(t);
        setTimeout(() => { t.classList.add('is-leaving'); setTimeout(() => t.remove(), 300); }, 4000);
    }

    showLoading(show, msg) {
        document.getElementById('loadingOverlay')?.classList.toggle('hidden', !show);
        if (show) { const lt = document.getElementById('loadingText'); if (lt) lt.textContent = msg || '🤖 IA analizando suelo y ubicación...'; }
    }

    showResults() {
        const c = document.getElementById('resultsContainer');
        if (c) { c.classList.remove('hidden'); setTimeout(()=>c.scrollIntoView({behavior:'smooth',block:'start'}),100); }
    }

    hideResults() { document.getElementById('resultsContainer')?.classList.add('hidden'); }

    // ── GENERADOR DE PDF (diagnóstico de suelo) ──────────────────────────────
    downloadPDF() {
        if (this._resultsMode === 'plant') return this.downloadPlantPDF();
        if (!this.currentResults) {
            this.showToast('Primero genera un diagnóstico para descargar el PDF', 'warning');
            return;
        }
        if (typeof window.jspdf === 'undefined') {
            this.showToast('Librería PDF no disponible. Verifica tu conexión.', 'error');
            return;
        }
        this.showToast('Generando PDF...', 'info');
        try {
            const { jsPDF } = window.jspdf;
            const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
            const a = this.currentResults;
            const d = this.currentData || {};
            const now = new Date();
            const fecha = now.toLocaleDateString('es-MX', { year: 'numeric', month: 'long', day: 'numeric' });
            const userName = window.userData?.nombre || 'Usuario';
            const cap = (s) => { s = String(s ?? ''); return s.charAt(0).toUpperCase() + s.slice(1); };
            const K = makePdfKit(doc, { footerLabel: 'EcoScan - Diagnóstico de Suelos con IA', fecha });
            const { C, ML, CW } = K;
            const prec = a.precision || null, PP = (prec && prec.properties) || {};
            const sui = a.suitability || { level: 'N/A', score: 0, message: '' };
            const scColor = sui.level === 'Óptimo' ? C.dark : sui.level === 'Aceptable' ? C.warn : C.bad;
            const goals = { reforestacion: 'Reforestación', agricultura: 'Agricultura', ambos: 'Reforestación y agricultura' };
            const lvlColor = (l) => (l === 'alta' || l === 'muy alta' || l === 'alto' || l === 'crítico') ? C.bad : (l === 'media' || l === 'medio') ? C.warn : C.dark;
            const qColor = (l) => l === 'alta' ? C.dark : l === 'media' ? C.warn : C.bad;

            // ═══ PORTADA ═══
            K.banner('Diagnóstico Inteligente de Suelos con IA');
            let y = K.k.y;
            K.rect(ML, y, CW, 40, 4, C.light);
            K.font('bold', 9.5, C.dark); doc.text('Informe generado para', ML + 7, y + 8);
            K.font('bold', 13, C.txt); doc.text(K.fit(userName, CW - 62, 'bold', 13), ML + 7, y + 15.5);
            K.font('normal', 9, C.mut);
            doc.text('Fecha: ' + K.T(fecha), ML + 7, y + 22.5);
            if (d.region) doc.text(K.fit('Región: ' + d.region, CW - 62), ML + 7, y + 28);
            doc.text(K.fit('Tipo de suelo: ' + cap(String(d.soilType || 'N/A').replace(/_/g, ' ')), CW - 62), ML + 7, y + 33.5);
            // marcador de aptitud
            const bx = ML + CW - 44;
            K.rect(bx, y + 5, 38, 30, 4, C.white);
            K.font('bold', 7.5, C.mut); doc.text('APTITUD DEL SUELO', bx + 19, y + 11, { align: 'center' });
            K.font('bold', 22, scColor); doc.text(String(Math.round(Number(sui.score) || 0)), bx + 19, y + 23, { align: 'center' });
            K.font('normal', 8, C.mut); doc.text('/ 100', bx + 19, y + 29.5, { align: 'center' });
            K.k.y = y + 47;

            K.chips([{ t: String(sui.level || 'N/A').toUpperCase(), color: scColor }].concat(prec && prec.dataQuality ? [{ t: 'DATOS: CALIDAD ' + String(prec.dataQuality.level).toUpperCase() + ' ' + prec.dataQuality.score + '/100', color: qColor(prec.dataQuality.level) }] : []));
            K.para(sui.message, { color: C.mut, size: 9.3 });

            // datos clave en una cuadrícula de 3 columnas
            const facts = [
                ['pH', String(PP.ph && PP.ph.value != null ? PP.ph.value : 'sin dato')], ['Compactación', d.compaction || 'N/A'], ['Drenaje', d.drainage || 'N/A'],
                ['Erosión', d.erosion || 'N/A'], ['Incendios', d.fireHistory || 'N/A'], ['Objetivo', goals[d.goal] || d.goal || 'N/A']
            ];
            const fw = CW / 3, fh = 11;
            K.ensure(fh * 2 + 4);
            y = K.k.y;
            K.rect(ML, y, CW, fh * 2 + 2, 3, C.bg);
            facts.forEach(([k, v], i) => {
                const cx = ML + (i % 3) * fw + 5, cy = y + Math.floor(i / 3) * fh + 3.6;
                K.font('normal', 7.2, C.mut); doc.text(K.T(k).toUpperCase(), cx, cy);
                K.font('bold', 9, C.txt); doc.text(K.fit(v, fw - 8, 'bold', 9), cx, cy + 4.2);
            });
            K.k.y = y + fh * 2 + 6;
            K.para('Análisis con IA sobre datos con procedencia: medidos, modelados y estimados, cada uno etiquetado.', { size: 8, color: [0, 100, 80], style: 'italic', after: 4 });

            // ═══ 1 · ESTADO DEL SUELO ═══
            if (a.phAnalysis || a.soilHealth || (a.mainIssues || []).length) {
                K.section('Estado del suelo');
                K.kv('Química', a.phAnalysis);
                K.kv('Salud general', a.soilHealth);
                if ((a.mainIssues || []).length) { K.gap(1); K.sub('Problemas detectados'); K.bullets(a.mainIssues); }
                K.gap(2);
            }

            // ═══ 2 · SALINIDAD Y NUTRIENTES ═══
            const sal = a.salinity, nut = a.nutrients;
            if (sal || nut) {
                K.section('Salinidad y nutrientes');
                if (sal) {
                    const fig = sal.estimatedDsM != null
                        ? sal.estimatedDsM + ' dS/m' + (sal.interval ? ' (90 %: ' + sal.interval[0] + '-' + sal.interval[1] + ')' : '') + (sal.classLabel ? ' · ' + sal.classLabel : '')
                        : 'sin cifra en dS/m: no hay medición de conductividad';
                    K.chips([{ t: 'SALINIDAD ' + String(sal.level || 'N/A').toUpperCase(), color: lvlColor(sal.level) }]);
                    K.para(fig, { color: C.mut, size: 9, after: 1 });
                    K.para('Procedencia: ' + (PROV_PLAIN[sal.source] || 'Estimado por IA') + (sal.basis ? ' · ' + sal.basis : ''), { size: 8.3, color: C.mut });
                    K.para(sal.impact);
                    if (sal.recommendation) K.card({ title: 'Recomendación', blocks: [{ text: sal.recommendation, color: C.txt }], fill: C.tint });
                }
                if (nut) {
                    const dt = nut.detail || {};
                    const nutTxt = (key) => {
                        const lvl = nut[key], x = dt[key];
                        if (!lvl || lvl === 'sin dato') return 'Sin medición' + (x && x.potential ? ' (potencial según la materia orgánica: ' + x.potential + ')' : '');
                        return cap(lvl) + (x && x.value != null ? ' · ' + x.value + ' ' + x.unit + (x.class ? ' (' + x.class + ')' : '') : '');
                    };
                    // La etiqueta de procedencia solo se añade cuando hay un dato (si no hay medición ya lo dice el texto)
                    const srcTag = (x) => (x && x.source && x.source !== 'none' && PROV_PLAIN[x.source] ? '  [' + PROV_PLAIN[x.source] + ']' : '');
                    K.sub('Estado nutricional');
                    const omD = dt.organicMatter;
                    K.kv('Nitrógeno', nutTxt('nitrogen') + srcTag(dt.nitrogen));
                    K.kv('Fósforo', nutTxt('phosphorus') + srcTag(dt.phosphorus));
                    K.kv('Potasio', nutTxt('potassium') + srcTag(dt.potassium));
                    K.kv('Materia orgánica', cap(nut.organicMatter || 'sin dato') + (omD && omD.value != null ? ' · ' + omD.value + ' % (' + omD.class + ')' : '') + srcTag(omD));
                    K.para(nut.analysis, { color: C.mut, size: 9 });
                }
                K.gap(2);
            }

            // ═══ 3 · PLAN DE REHABILITACIÓN ═══
            const planGroups = [['Acciones inmediatas', a.immediateActions], ['Estrategias a largo plazo', a.longTermActions], ['Enmiendas recomendadas', a.amendments]].filter((g) => (g[1] || []).length);
            if (planGroups.length) {
                K.section('Plan de rehabilitación');
                planGroups.forEach((g) => { K.sub(g[0]); K.bullets(g[1]); });
                K.gap(1);
            }

            // ═══ 4 · PLAGAS Y ENFERMEDADES ═══
            if (a.pests) {
                const pe = a.pests, rk = String(pe.riskLevel || 'bajo').toLowerCase();
                K.section('Plagas y enfermedades', 30);
                K.chips([{ t: 'RIESGO GENERAL: ' + rk.toUpperCase(), color: lvlColor(rk) }]);
                (pe.commonPests || []).forEach((p, i) => K.card({
                    title: (i + 1) + '. ' + (p.name || ''), tag: p.type || '', tagColor: C.med, accent: lvlColor(rk),
                    blocks: [{ text: p.description, color: C.mut }, { text: p.control ? 'Control: ' + p.control : '', color: C.fix }]
                }));
                if ((pe.preventionTips || []).length) { K.sub('Consejos de prevención'); K.bullets(pe.preventionTips); }
                K.gap(1);
            }

            // ═══ 5 · AGUA Y RUTINA DE CUIDADO ═══
            const wm = a.waterManagement, cr = a.careRoutine;
            if (wm || (cr && ((cr.soilCare || []).length || (cr.plantCare || []).length || cr.schedule))) {
                K.section('Agua y rutina de cuidado');
                if (wm) {
                    K.kv('Tipo de riego', wm.irrigationType);
                    K.kv('Frecuencia', wm.frequency);
                    if ((wm.tips || []).length) K.bullets(wm.tips);
                }
                if (cr) {
                    if ((cr.soilCare || []).length) { K.sub('Cuidado del suelo'); K.bullets(cr.soilCare); }
                    if ((cr.plantCare || []).length) { K.sub('Cuidado de las plantas'); K.bullets(cr.plantCare); }
                    if (cr.schedule) K.card({ title: 'Calendario', blocks: [{ text: cr.schedule, color: C.txt }], fill: C.tint });
                }
                K.gap(1);
            }

            // ═══ 6 · PLANTAS RECOMENDADAS ═══
            const pl = a.plants || {};
            if ((pl.reforestation || []).length || (pl.agriculture || []).length || (pl.coverCrops || []).length) {
                K.section('Plantas recomendadas para tu región', 30);
                if ((pl.reforestation || []).length) {
                    K.sub('Reforestación / especies nativas');
                    pl.reforestation.forEach((p) => K.card({
                        title: p.name || '', fill: C.light, accent: C.med,
                        blocks: [{ text: p.reason, color: C.mut }, { text: [p.nativeRegion ? 'Región: ' + p.nativeRegion : '', p.waterNeeds ? 'Agua: ' + p.waterNeeds : ''].filter(Boolean).join('  |  '), color: C.mut, size: 8 }, { text: p.care ? 'Cuidados: ' + p.care : '', color: C.fix }]
                    }));
                }
                if ((pl.agriculture || []).length) {
                    K.gap(1); K.sub('Agricultura / cultivos recomendados');
                    pl.agriculture.forEach((p) => K.card({
                        title: p.name || '', fill: C.light, accent: C.med,
                        blocks: [{ text: p.reason, color: C.mut }, { text: [p.season ? 'Época: ' + p.season : '', p.yield ? 'Rinde: ' + p.yield : ''].filter(Boolean).join('  |  '), color: C.mut, size: 8 }, { text: p.care ? 'Cuidado: ' + p.care : '', color: C.fix }]
                    }));
                }
                if ((pl.coverCrops || []).length) {
                    K.gap(1); K.sub('Cultivos de cobertura');
                    pl.coverCrops.forEach((p) => K.card({
                        title: p.name || '', fill: C.light, accent: C.med,
                        blocks: [{ text: p.benefit, color: C.mut }, { text: p.howToUse ? 'Cómo usarlo: ' + p.howToUse : '', color: C.fix }]
                    }));
                }
                K.gap(1);
            }

            // ═══ 7 · CALIDAD Y PROCEDENCIA DE LOS DATOS ═══
            if (prec && prec.dataQuality) {
                const dq = prec.dataQuality;
                K.section('Calidad y procedencia de los datos', 40);
                K.chips([{ t: 'CALIDAD ' + String(dq.level || 'N/A').toUpperCase() + '  ' + (dq.score ?? 0) + '/100', color: qColor(dq.level) }]);
                K.para(dq.message, { color: C.mut, size: 9 });
                const rows = [];
                [['ph', 'pH (agua)'], ['ec', 'Conductividad'], ['om', 'Materia orgánica'], ['clay', 'Arcilla'], ['sand', 'Arena'], ['silt', 'Limo'], ['cec', 'CIC'], ['n', 'N inorgánico'], ['p', 'Fósforo'], ['k', 'Potasio']].forEach(([key, name]) => {
                    const p = PP[key];
                    if (!p) return;
                    const has = p.value != null;
                    const km = (p.sources || []).find((s) => s.src === 'nearby' && s.km != null);
                    rows.push([
                        { t: name, bold: true },
                        { t: has ? `${p.value}${p.unit ? ' ' + p.unit : ''}${p.low != null && p.high != null && p.low !== p.high ? ' (' + p.low + '-' + p.high + ')' : ''}` : 'Sin medición', color: has ? C.txt : C.mut },
                        { t: (PROV_PLAIN[p.primary] || 'Sin medición') + (km ? ' ' + km.km + ' km' : ''), color: has && ['lab', 'field'].includes(p.primary) ? C.dark : C.mut },
                        { t: has ? ((p.class || '-') + (p.confidence && p.confidence !== 'sin dato' ? ' · ' + p.confidence : '')) : '-', color: C.mut }
                    ]);
                });
                if (rows.length) K.table([{ label: 'Propiedad', w: 38 }, { label: 'Valor (margen 90 %)', w: 52 }, { label: 'Procedencia', w: 48 }, { label: 'Clase / confianza', w: 40 }], rows);
                if (prec.texture) K.kv('Textura', prec.texture.es + ' (arcilla ' + prec.texture.clay + ' %, arena ' + prec.texture.sand + ' %, limo ' + prec.texture.silt + ' %) · ' + (PROV_PLAIN[prec.texture.primary] || ''));
                if (prec.nearest && prec.nearest.found) {
                    const nr = prec.nearest, nv = nr.values || {};
                    K.kv('Perfil medido', 'a ' + nr.km + ' km · ' + nr.source + (nr.date ? ' · ' + String(nr.date).slice(0, 4) : '') + (nv.ph != null ? ' · pH ' + nv.ph : '') + (nv.ec != null ? ' · CE ' + nv.ec + ' dS/m' : '') + (nv.om != null ? ' · M.O. ' + nv.om + ' %' : ''));
                } else if (prec.providers && Object.keys(prec.providers).length) {
                    K.kv('Perfil medido', 'ninguno a menos de ' + ((prec.nearest && prec.nearest.searchedKm) || 30) + ' km de tu ubicación');
                }
                const sg = a.soilGrids;
                if (sg) K.kv('Satélite (' + (sg.provider === 'soildb' ? 'OpenLandMap-soildb' : sg.provider === 'openlandmap' ? 'OpenLandMap' : 'SoilGrids') + ')', [sg.ph_real != null ? 'pH ' + sg.ph_real : '', sg.clay_pct != null ? 'arcilla ' + sg.clay_pct + ' %' : '', sg.sand_pct != null ? 'arena ' + sg.sand_pct + ' %' : '', sg.silt_pct != null ? 'limo ' + sg.silt_pct + ' %' : '', sg.organic_carbon_pct != null ? 'C. orgánico ' + sg.organic_carbon_pct + ' %' : '', sg.cec_cmolkg != null ? 'CIC ' + sg.cec_cmolkg + ' cmol/kg' : ''].filter(Boolean).join(' · ') + ' (modelo de ' + (sg.res_m || 250) + ' m, capa ' + (sg.depth || '0-5 cm') + ', no medición)');
                const cl = a.climate;
                if (cl) K.kv('Clima del sitio', [cl.elevation_m != null ? 'altitud ' + cl.elevation_m + ' m' : '', cl.koppen ? 'Köppen ' + cl.koppen + (cl.koppenName ? ' (' + cl.koppenName + ')' : '') : '', cl.mat_c != null ? 'temp. media ' + cl.mat_c + ' °C' : '', cl.map_mm != null ? 'lluvia ' + cl.map_mm + ' mm/año' : '', cl.frost || '', cl.wet_months ? 'lluvias: ' + cl.wet_months : ''].filter(Boolean).join(' · ') + ' (NASA POWER, normales de largo plazo)');
                if ((prec.conflicts || []).length) { K.sub('Contradicciones detectadas'); K.bullets(prec.conflicts.map((c) => c.msg)); }
                if ((dq.howToImprove || []).length) { K.sub('Cómo subir la precisión'); K.bullets(dq.howToImprove); }
                K.para('Las mediciones directas pesan más que los modelos satelitales y estos más que las estimaciones de la IA por foto. Los datos satelitales (OpenLandMap-soildb / SoilGrids) son un modelo (30-250 m), no una medición de tu terreno. Clases según la NOM-021-SEMARNAT-2000 cuando aplica.', { size: 8, color: C.mut, style: 'italic' });
            }

            const total = K.footers();
            const filename = 'EcoScan_Diagnostico_' + (d.region ? K.T(d.region.split(',')[0]).normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_|_$/g, '') : 'Suelo') + '_' + now.getFullYear() + String(now.getMonth() + 1).padStart(2, '0') + String(now.getDate()).padStart(2, '0') + '.pdf';
            doc.save(filename);
            this.showToast(`✅ PDF descargado (${total} página${total > 1 ? 's' : ''})`, 'success');
        } catch (err) {
            console.error('PDF error:', err);
            this.showToast('Error al generar el PDF: ' + err.message, 'error');
        }
    }
}

document.addEventListener('DOMContentLoaded', () => { window.ecoscan = new EcoScanApp(); });

const _s = document.createElement('style');
_s.textContent = `@keyframes ecSlideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}`;
document.head.appendChild(_s);