---
name: higgsfield-espera
description: 'Espera a que terminen las generaciones de Higgsfield (imágenes, videos, audio) sin gastar tokens: un script habla directo con el conector del plan y avisa cuando quedan, con las URLs. Se usa SIEMPRE que se lanzó generate_image_batch, generate_video_batch, generate_audio_batch o un generate_* y toca esperar a que terminen, en vez de llamar jobs_wait una y otra vez en la sesión. Conector del plan (créditos de la web), no la API prepagada.'
---

# higgsfield-espera

`jobs_wait` del conector de Higgsfield espera máximo 15 s por llamada, así que esperar un video son decenas de llamadas. Hechas desde la sesión (Opus, contexto enorme) cada una cuesta releer toda la conversación: en 14 días fueron 1,206 llamadas en 13 sesiones (una sola sesión: 268). El script `higgsfield_espera.py` hace esas llamadas solo, sin Claude: cero tokens. Esperar no gasta créditos.

## Regla

Después de lanzar las generaciones, **no llames `jobs_wait` tú**. Corre UNA vez, con `run_in_background: true`:

```bash
python3 "<carpeta de este skill>/higgsfield_espera.py" esperar <job_id> [<job_id> ...] --voz "Ya quedaron los videos"
```

Los job_id van en el orden de sus `index` (el primero es index 0). La app avisa cuando termina. La salida trae una línea por job (`index  estado  url-o-error`) y al final `LISTO` o `NO TERMINÓ`. Luego muestra todo junto con **una** llamada a `show_generation_by_ids` y sigue.

- **Sale 2 / `FALTA LOGIN`:** la Mac no tiene permiso todavía, o se venció. Corre `higgsfield_espera.py login --correo <correo de la cuenta de Higgsfield>` (el de la cuenta donde está el plan de Higgsfield; si no lo sabes, pregúntalo); abre la página de permiso en el Chrome de esa cuenta, la persona pica Permitir, y queda en el Llavero (servicio `higgsfield-espera`). Mientras, usa el respaldo de abajo.
- **`failed` o `NO TERMINÓ`:** dilo tal cual; no relances la generación sin OK (gasta créditos).

## Respaldo: agente Haiku

Si el script no puede (sin login y nadie para picar Permitir), lanza un Agent con `model: "haiku"` y este prompt:

```
Espera a que terminen estas generaciones de Higgsfield. Carga con ToolSearch la herramienta
`mcp__higgsfield__jobs_wait` (query "select:mcp__higgsfield__jobs_wait") y llámala con estos jobs
(máximo 12 por llamada): <lista de {index, job_id}>. Repite con timeout_seconds=15 hasta que
all_terminal sea true. No llames otra herramienta de Higgsfield, no generes nada, no reintentes un
job con lookup_failed. Tope: 60 llamadas por grupo. Responde SOLO con una tabla: index, job_id,
estado final y la URL o el error.
```

Cada agente arranca con ~130k tokens de herramientas (en Haiku, barato pero no gratis).

## Notas

- Usa el conector del plan; no toca la API prepagada (a propósito: esa cobra de un saldo aparte).
- El login es por persona y por Mac: cada quien entra con su propia cuenta de Higgsfield.
- Instalar: clona este repo en `~/.claude/skills/higgsfield-espera` y corre `login` una vez. Solo usa Python 3 de macOS (sin dependencias).
- Probado 20-oct-2026: login en vivo con una cuenta real y espera de un job real ya terminado (1 vuelta, 1 s). El ciclo de varias vueltas, la renovación de la sesión y la sesión vencida están probados con un Higgsfield falso: `test_higgsfield_espera.py`. Falta verlo con un video real que tarde minutos.
