# LOT-Bot · Anuncio TikTok (40 s, 9:16)

**Concepto:** «Tú vendes. LOT-Bot publica.» Del agobio de publicar anuncios uno por uno al alivio de verlo hecho mientras te tomas un café.
**Público:** vendedores habituales de Wallapop (muebles, segunda mano, pequeños negocios) con decenas o cientos de productos.
**Tono:** directo, cercano, ritmo rápido. Subtítulos grandes (casi todo el mundo ve TikTok sin sonido).

| Tiempo | Escena | En pantalla | Voz en off (opcional) | Sonido |
|---|---|---|---|---|
| 0–4 s | **Hook** | Reloj girando a toda velocidad. «¿Sigues perdiendo **HORAS** publicando anuncios en Wallapop **uno por uno?**» (palabra a palabra) | «¿Sigues perdiendo horas publicando anuncios en Wallapop uno por uno?» | Golpe grave + tic-tac |
| 4–12 s | **Problema** | Móvil gris: se escriben título, descripción, precio y fotos; «Anuncio 1 de 120», reloj 16:00 → 19:08. Avance rápido, la imagen pierde color: «3 horas después… solo 14 de 120 😵» | «Título, descripción, precio, fotos… y vuelta a empezar. Tres horas después, solo llevas catorce.» | Tic-tac tenso, bajo descendente |
| 12–16 s | **Transición + marca** | Barrido circular violeta. «Hasta que descubres…» → logo LOT-Bot. «El software que publica y gestiona tus anuncios de Wallapop por ti» | «Hasta que descubres LOT-Bot.» | Whoosh + campanilla, entra el beat |
| 16–19 s | **Cómo funciona 1** | Cae `catalogo.csv` en la app y se llena la cola: «120 anuncios en cola» | «Sube tu catálogo…» | Beat |
| 19–22 s | **Cómo funciona 2** | Chat: «Publica los 120 en las cuentas 1 y 2» → plan → botón **Confirmar** | «…díselo con tus palabras, revisa el plan y confirma…» | Ping al confirmar |
| 22–25 s | **Cómo funciona 3** | Barra de progreso 0 → 120, checks verdes. «Y tú, mientras tanto… ☕» | «…y LOT-Bot publica por ti.» | Beat |
| 25–33 s | **Beneficios** | 4 tarjetas: ⚡ Cientos de anuncios · 👥 Multicuenta · 🤖 Asistente IA en español · 🛡️ Nada se publica sin tu confirmación. Remate: «Horas ➜ minutos» | «Cientos de anuncios, todas tus cuentas, un asistente que te entiende… y siempre con tu control.» | Ping por tarjeta |
| 33–40 s | **CTA** | Logo · «Tú vendes. LOT-Bot publica.» · botón «Pruébalo gratis en MODO DEMO» · «👉 Link en la bio o escríbenos «LOTBOT» por DM» | «Tú vendes, LOT-Bot publica. Pruébalo gratis: link en la bio.» | Final con fundido |

## Texto para publicar en TikTok
> ¿120 anuncios y 3 horas perdidas? 😩 Con LOT-Bot subes tu catálogo, confirmas y él publica por ti en todas tus cuentas. Pruébalo gratis en modo demo 👉 link en la bio
> #wallapop #vendermas #segundamano #emprendedores #automatizacion #trucosdeventa #ia

## Notas
- Respeta las zonas seguras de TikTok: nada importante en el 20 % inferior ni en el borde derecho.
- La voz en off puede grabarse con la voz de texto a voz de TikTok pegando la columna «Voz en off».
- «Horas ➜ minutos» y «120 anuncios» son ejemplos ilustrativos; si se usan cifras concretas en campañas pagadas, que sean medidas reales.
- La pieza no usa logotipos ni colores de Wallapop (solo el nombre, para describir el servicio).
- Antes de publicar: poner el enlace real en la bio y confirmar que el modo demo se puede descargar desde ahí.

## Regenerar el vídeo
```bash
python3 musica.py                      # banda sonora → musica.wav
node render.js /tmp/frames 30 40       # fotogramas 1080x1920
ffmpeg -framerate 30 -i /tmp/frames/f%05d.jpg -i musica.wav -c:v libx264 -crf 20 -pix_fmt yuv420p -c:a aac -shortest LOT-Bot_TikTok.mp4
```
Abrir `anuncio.html` en el navegador muestra la animación en bucle.
