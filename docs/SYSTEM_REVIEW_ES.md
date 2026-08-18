**MAR FUTURA**

1\. Contexto

El sistema OceanKind está desplegado y funcionando. Detecta eventos, sube datos y envía alertas por WhatsApp. Esta revisión no trata de un sistema roto --- se trata de una **evaluación honesta de lo que debería mejorarse**, cuáles son los riesgos reales y dónde el coste de arreglar algo supera el beneficio.

TL;DR

**Tres cosas necesitan arreglo hoy (\~1.5 días):** Las credenciales de la API de Twilio están hardcodeadas en el código fuente, todo el almacén de datos (historial de detecciones, grabaciones de audio, coordenadas GPS) es accesible públicamente sin autenticación, y la ubicación física del sensor está hardcodeada en el código. Todo verificado durante esta revisión.

**Dos cosas deberían arreglarse pronto (\~10-15 días):** el sistema deja de escuchar mientras procesa audio (arquitectura incorrecta para monitoreo acústico, aunque la tasa real de eventos perdidos es baja), y el código ha acumulado deuda técnica (dos codebases divergentes, proceso OTA arriesgado). Son refactorizaciones sobre el hardware actual, no una reescritura.

**Todo lo demás puede esperar:** la Raspberry Pi es excesiva pero funciona, el dashboard es ineficiente pero carga, y la autenticación enterprise es innecesaria para un equipo pequeño. Una API backend es el puente entre los arreglos rápidos y la arquitectura a largo plazo --- resuelve la seguridad permanentemente y habilita escalabilidad futura --- pero no es urgente para un despliegue de una sola unidad. Entonces, ¿para cuándo esperamos desplegar más unidades, y cuántas más?

2\. Hardware

  2.1 Plataforma de Cómputo

    **\[PUEDE ESPERAR\]** La Raspberry Pi 4 está sobredimensionada. El modelo ML tiene 53 parámetros (3 KB) --- teóricamente podría correr en un ESP32. El consumo es \~3.5W donde una placa más pequeña podría usar \~0.4W.

      **Contraargumento:** La Pi 4 funciona, está desplegada, y el panel solar aguanta la carga. Ahorrar 2.5W y \$40 por unidad solo importa si se construyen 10+ unidades. La ruta ESP32 requiere reescribir toda la pipeline de extracción de features en C --- de forma realista 2-3 meses, no 1-2 semanas. La Pi Zero 2W es una migración más ligera pero la fragilidad de la tarjeta SD persiste. Nada de esto vale la pena para un solo despliegue.

      **Esfuerzo:** Pi Zero 2W: 4-7 días. ESP32: 2-3 meses. Recomendamos postergar hasta confirmar despliegue multi-unidad.

      **PD:** El código referencia un HifiBerry DAC+ ADC Pro; un diagrama del sistema separado muestra un Raspberry Pi Codec Zero. Cuál ADC está realmente desplegado necesita confirmarse antes de cualquier cambio de hardware.

  2.2 Fragilidad de la Tarjeta SD

    **\[DEBERÍA ARREGLARSE\]** Las tarjetas MicroSD se degradan con escrituras repetidas y son vulnerables a corrupción por pérdida de energía. El equipo ha mitigado esto con un overlay filesystem, pero esto añade complejidad e introduce sus propios modos de fallo durante actualizaciones OTA.

      **Contraargumento:** La mitigación del overlay FS ya está implementada y funcionando. Es un riesgo conocido, no un fallo activo. Se vuelve crítico si la unidad pierde energía durante una actualización OTA (el proceso de dos reinicios puede dejar el nodo inoperativo). En ESP32 este problema desaparece por completo --- pero esa es una migración mucho mayor.

      **Esfuerzo:** Añadir health-check con rollback al script OTA: 2-3 días.

3\. Arquitectura de Software

  3.1 Brechas de Detección (Ventana Sorda)

    **\[DEBERÍA ARREGLARSE\]** El sistema graba un clip de 5 segundos, luego deja de escuchar mientras clasifica y sube datos. Durante el procesamiento (3-15 segundos), el hidrófono está sordo.

      **Contraargumento:** La probabilidad de perder una explosión específica es baja. Las explosiones son eventos infrecuentes y la ventana sorda es corta. El sistema está capturando detecciones hoy --- tres en la sesión actual. Pero la arquitectura es fundamentalmente incorrecta para monitoreo acústico: nunca debería dejar de escuchar. La solución es reestructurar el código en una pipeline asíncrona en la Pi 4 actual, no una reescritura en hardware nuevo.

      **Esfuerzo:** 5-10 días. Refactorizar el monolito en módulos con hilos. El codebase deprecado ya tiene un módulo de captura funcional como base.

  3.2 Monolito de un Solo Archivo (1,309 Líneas)

    **\[DEBERÍA ARREGLARSE\]** Todo el sistema --- captura de audio, clasificación ML, alertas WhatsApp, subida de blobs, telemetría solar, polling del modem, monitoreo de batería --- es un solo archivo Python. Cambios en cualquier parte arriesgan romper todo lo demás.

      **Contraargumento:** Un monolito no es inherentemente malo. Es más simple de desplegar, más simple de depurar, y solo hay una unidad. La modularización se vuelve necesaria al implementar la pipeline asíncrona (Sección 3.1), momento en el que ocurre naturalmente como parte de la refactorización.

      **Esfuerzo:** Incluido en el trabajo de la pipeline asíncrona (Sección 3.1).

  3.3 Dos Codebases Divergentes

  **\[DEBERÍA ARREGLARSE\]** El repositorio contiene un monolito en producción y una versión modular deprecada. El script de setup instala la versión incorrecta. El requirements.txt lista dependencias del código deprecado. Un desarrollador nuevo no sabría qué archivos se ejecutan.

      **Contraargumento:** Es un problema de mantenibilidad, no operacional --- la Pi desplegada ejecuta el código correcto. Pero es barato de arreglar y evita confusión real.

      **Esfuerzo:** 2-3 días. Archivar código deprecado, arreglar setup.sh y requirements.txt, añadir un README.

  3.4 Riesgo de Actualización OTA

  **\[DEBERÍA ARREGLARSE\]** El proceso de actualización over-the-air requiere dos reinicios y deshabilita temporalmente el overlay filesystem. Si el proceso falla a mitad (caída de red, pérdida de energía), la unidad puede quedar en un estado irrecuperable sin mecanismo de rollback.

      **Contraargumento:** Las actualizaciones son infrecuentes y presumiblemente se realizan en condiciones estables. El riesgo es real pero de baja probabilidad. Un health-check con rollback automático lo haría seguro.

      **Esfuerzo:** 2-3 días. Añadir un watchdog que revierta si el servicio falla al iniciar tras la actualización.

4\. Dashboard y Escalabilidad

  4.1 El Dashboard Descarga Todo, Siempre

  **\[PUEDE ESPERAR\]** El dashboard descarga el historial completo de detecciones (manifest.json), estado completo e historial completo de energía cada 30 segundos. Sin paginación, sin filtros, sin peticiones condicionales.

      **Contraargumento:** Funciona. Una unidad, un equipo pequeño, un archivo JSON que carga en un segundo. Los problemas de escalabilidad son reales pero son problemas futuros. Si se construye la API backend (Sección 5.2), estas mejoras vienen naturalmente como parte de la migración a datos servidos por API.

      **Esfuerzo:** 5-10 días sobre la API. Solo vale la pena después de que exista el backend.

  4.2 Sin Soporte Multi-Dispositivo

  **\[PUEDE ESPERAR\]** El patrón lectura-modificación-escritura de manifest.json fallará con múltiples dispositivos (condición de carrera: escrituras simultáneas pierden datos). El dashboard no tiene selector de dispositivo.

      **Contraargumento:** Hay un solo dispositivo. Es una limitación de diseño real pero causa cero problemas hoy. Abordar cuando se planifique la segunda unidad, no antes.

      **Esfuerzo:** Incluido en API + reconstrucción del dashboard. No tiene sentido un arreglo aislado.

5\. Seguridad y Autenticación

  5.1 Credenciales de Twilio en el Código Fuente

  **\[ARREGLAR YA\]** El SID de cuenta y el token de autenticación de Twilio están hardcodeados como valores por defecto en el código Python. Cualquiera con acceso al código puede enviar mensajes WhatsApp en la cuenta OceanKind.

      Sin contraargumento. Esto es indefendible. Rotar hoy.

      **Esfuerzo:** Medio día.

  5.2 Todos los Datos Son Públicamente Accesibles

  **\[ARREGLAR YA\]** El contenedor de Azure Blob Storage está configurado como lectura anónima pública. Esto fue hecho intencionalmente: el dashboard estático no tiene backend, así que lee blobs directamente --- lo cual solo funciona si el contenedor es público. La consecuencia es que cualquiera con la URL de la cuenta de almacenamiento puede acceder al historial completo de detecciones, todas las grabaciones de audio, telemetría en tiempo real, y las coordenadas GPS exactas del hardware del sensor. Lo verificamos durante esta revisión --- todos los datos son descargables ahora mismo sin autenticación.

      **Contraargumento:** El modelo de amenaza importa aquí. El escenario de \"adversario sofisticado monitoreando patrones de detección\" es probablemente extremo --- pescadores con explosivos en la costa rural de Chile es improbable que estén vigilando un endpoint de Azure. Las coordenadas GPS y la exposición de ubicación física es el riesgo más concreto (robo, vandalismo de equipos remotos). La exposición de datos es real y verificada, pero la urgencia depende de quién realmente podría mirar.

      La solución es un cambio de un click en el Portal de Azure (configurar contenedor a Privado). Esto rompe el dashboard hasta que se añada un SAS token de solo lectura a sus llamadas fetch --- un arreglo interino de 1 día. La solución permanente es una API backend que controle todo el acceso a través de endpoints autenticados. **La falta de un backend es la causa raíz de toda la exposición de seguridad.**

      **Esfuerzo:** Interino (SAS token): 1 día. Permanente (API backend): 10-15 días.

  5.3 Coordenadas GPS en el Código Fuente

  **\[ARREGLAR YA\]** Latitud y longitud del sensor están hardcodeadas en el código Python y se suben en cada actualización de estado. La ubicación física de equipos remotos es públicamente descubrible.

      Sin contraargumento. Mover a archivo de entorno. Arreglo trivial.

      **Esfuerzo:** 1 hora.

  5.4 Dashboard Sin Login

  **\[DEBERÍA ARREGLARSE\]** El dashboard es una página HTML estática sin autenticación alguna. Cualquiera con la URL tiene acceso completo de lectura a todos los datos del sistema.

      **Contraargumento:** Si el contenedor blob se hace privado y el dashboard usa un SAS token, la URL del dashboard por sí sola ya no da acceso a datos crudos --- el SAS token está embebido en el JavaScript. Esto es seguridad por oscuridad, no autenticación real, pero para un equipo pequeño es un paso interino pragmático. La autenticación real del dashboard viene con la API backend.

      **Esfuerzo:** Incluido en la construcción de la API. Tokens de invitación simples son suficientes --- OAuth y RBAC son excesivos para un equipo pequeño.

  5.5 Configuración Remota Sin Firmar

  **\[DEBERÍA ARREGLARSE\]** La Pi consulta un blob remote\_config.json y aplica sus valores (umbrales de detección, parámetros de grabación). Si alguien obtiene acceso de escritura al contenedor, puede alterar silenciosamente el comportamiento del sistema.

      **Contraargumento:** El acceso de escritura a la cuenta de almacenamiento Azure requiere la storage key o un SAS token con alcance de escritura. Si un atacante tiene eso, la configuración sin firmar es el menor de los problemas. Esto se vuelve relevante cuando la API maneje la entrega de configuración --- en ese punto, firmar es barato de añadir.

      **Esfuerzo:** Incluido en la construcción de la API.

6\. Resumen

  --------- ------------------------------------------ ----------------- -----------------------------------------------------------------------
  **§**     **Ítem**                                   **Esfuerzo**      **Valoración Honesta**
  2.1       Plataforma de cómputo (Pi 4 excesiva)      4-7d / 2-3mo      Funciona bien. Solo revisitar para despliegue multi-unidad.
  2.2       Fragilidad tarjeta SD                       2-3 días          Mitigado por overlay FS. Añadir rollback OTA.
  
  3.1       Brechas de detección (ventana sorda)        5-10 días         Tasa de pérdida baja hoy, pero arquitectura incorrecta. Arreglar en hardware actual.
  3.2       Monolito (1,309 líneas)                     (incl. 3.1)       Modularizar como parte del refactor asíncrono.
  3.3       Dos codebases divergentes                   2-3 días          Limpieza barata. Evita confusión.
  3.4       Riesgo actualización OTA                    2-3 días          Baja probabilidad pero irrecuperable. Añadir rollback.

  4.1       Dashboard descarga todo                     5-10 días         Funciona hoy. Reconstruir cuando exista la API.
  4.2       Sin soporte multi-dispositivo               (incl. 4.1)       Un dispositivo. Abordar cuando se planifique la segunda unidad.
  **5.1**   **Credenciales Twilio en código**           **Medio día**     **Sin debate. Rotar hoy.**
  **5.2**   **Contenedor blob público + GPS expuesto**  **1d / 10-15d**   **Arreglo interino SAS: 1 día. API permanente: 10-15 días.**
  **5.3**   **GPS en código fuente**                    **1 hora**        **Trivial. Mover a archivo de entorno.**
  5.4       Dashboard sin login                         (incl. API)       SAS token es interino pragmático. Auth real viene con la API.
  5.5       Config remota sin firmar                    (incl. API)       Requiere storage key para explotar. Firmar cuando la API maneje config.
  --------- ------------------------------------------ ----------------- -----------------------------------------------------------------------
