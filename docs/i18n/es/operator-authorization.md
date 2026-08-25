---
slug: operator-authorization
source: docs/OPERATOR_AUTHORIZATION.md
source_sha256: sha256:543fc9c31e1ffe8e120aa726c819f0e9180d0f6ca92b28c9b4ce549d0025d4b1
---

# Autorización del operador

La autorización del operador protege cambios sensibles del plano de control en Settings. Es independiente del acceso a la UI Web ordinaria: explorar agentes, terminales, documentación y estadísticas no requiere el secreto de operador.

Esta función no es autenticación de usuario remoto. Mantén ThreadCells solo en loopback y sigue [Acceso remoto](REMOTE_ACCESS.md) cuando otra máquina necesite acceso.

## Cómo funciona

ThreadCells almacena un verificador derivado del secreto, nunca el secreto en texto plano. El servidor carga ese verificador al iniciar. Introducir el secreto correcto crea una sesión de operador segura y de corta duración; las mutaciones protegidas permanecen bloqueadas tras expirar.

```text
Verifier configured
      ↓
Settings shows Locked
      ↓ enter operator secret
Unlock operator changes
      ↓
Short-lived authenticated session
      ↓ expires
Locked again
```

La longitud mínima del secreto de operador es exactamente **5 caracteres**. Cuatro caracteres se rechazan. Se recomienda encarecidamente un secreto más largo, generado aleatoriamente.

## Crear un verificador

Ejecuta el comando independiente como usuario administrativo desde cualquier directorio de trabajo legible:

```bash
threadcells operator create-verifier --output /etc/threadcells/operator-verifier.json
```

El comando solicita el secreto sin mostrarlo y escribe solo el verificador KDF con sal. Protege el directorio contenedor de modificaciones por la cuenta de servicio de ThreadCells, permitiendo a la vez que esa cuenta lea el archivo. Una disposición adecuada es:

```bash
sudo chown root:threadcells /etc/threadcells
sudo chmod 0750 /etc/threadcells
sudo chown root:threadcells /etc/threadcells/operator-verifier.json
sudo chmod 0640 /etc/threadcells/operator-verifier.json
```

Adapta el nombre del grupo a la cuenta de servicio que use tu instalación. Cada directorio padre de la ruta también debe ser confiable: ThreadCells rechaza un verificador alcanzado mediante un directorio propiedad del servicio o escribible por grupo o por todos.

No pongas el secreto ni el JSON del verificador en el repositorio, la base de datos, registros, almacenamiento del navegador, telemetría ni en una solicitud API fuera de la operación de desbloqueo.

## Configurar el servidor

Establece la referencia absoluta al verificador en el entorno del servidor:

```bash
THREADCELLS_OPERATOR_VERIFIER_FILE=/etc/threadcells/operator-verifier.json
```

Reinicia solo el servidor ThreadCells e inspecciona Settings → General → Operator authorization. El estado debería ser **Configured · Locked**, no **Not configured** ni **Configuration invalid**.

El endpoint de sesión informa solo estado seguro:

```bash
curl -s http://127.0.0.1:9889/operator/session
```

El resultado esperado incluye `"configured": true` y `"authenticated": false` antes de desbloquear. Nunca devuelve la ruta del verificador, la sal, el hash ni el secreto.

## Desbloquear cambios protegidos

En Settings, introduce el secreto y elige **Unlock operator changes**. La ventana autenticada predeterminada es de cinco minutos. La UI muestra la expiración y vuelve al estado bloqueado cuando la sesión termina.

Las llamadas a Settings protegidas fallan mientras está bloqueado y tienen éxito durante la sesión autenticada. El navegador usa la cookie de sesión segura y de corta duración del servidor; no conserva el secreto de operador.

Full Cleanup reutiliza exactamente esta autoridad. La vista previa sigue disponible como inspección de seguridad de solo lectura, mientras que la ejecución exige la sesión de operador vigente y la confirmación estándar de acción permanente. La confirmación no vuelve a pedir el secreto. No existe ningún secreto de limpieza separado, credencial en URL, valor en el almacenamiento del navegador ni copia duradera en texto plano; la caducidad, el nuevo bloqueo y los límites de frecuencia no cambian.

## Sustituir el secreto

Crea un verificador nuevo en una ruta administrativa temporal, valida su propiedad y permisos, después sustituye atómicamente el archivo configurado y reinicia ThreadCells. Las sesiones de operador existentes deben considerarse no válidas tras la sustitución.

La UI Web actual intencionadamente no ofrece un restablecimiento remoto no autenticado ni un escritor de verificadores en Settings. El aprovisionamiento por CLI mantiene el verificador bajo propiedad del sistema operativo y evita crear un subsistema de seguridad más amplio.

## Lanzamiento Owner XHigh

El perfil integrado `critical_sol_xhigh_owner` está disponible mediante **Create Session & Spawn Agent**, **Add Agent** para una sesión existente y la CLI local. Ambos flujos Web muestran la misma advertencia de autoridad excepcional, requieren confirmación explícita y una sesión de operador desbloqueada, acuñan una capacidad de un solo uso de corta duración ligada a revisión y ámbito, y la consumen a través de la ruta de lanzamiento normal. Add Agent vincula la capacidad a la sesión existente y a su directorio de trabajo resuelto canónicamente; el operador no puede escribir una ruta de reemplazo arbitraria.

La ruta de CLI local requiere `--owner-xhigh` y una confirmación interactiva explícita. Acuña y consume la misma clase de capacidad de un solo uso a través de loopback. No existe una omisión reutilizable por encabezado: una casilla/confirmación ausente, secreto de operador ausente o incorrecto, ámbito que no coincide o concesión reutilizada falla de forma cerrada. El cliente Web autenticado recibe la capacidad opaca una sola vez únicamente para realizar el lanzamiento correspondiente; el secreto de operador nunca se devuelve. Ninguno de estos valores se copia en metadatos de agente o sesión, prompts de proveedor, transcripciones de terminal, registros ni almacenamiento del navegador. Estas rutas de lanzamiento no autorizan a hijos ni debilitan las mutaciones protegidas de Settings.

## Solución de problemas

- **No configurado:** la variable de entorno está ausente o vacía. Confirma que llega al proceso real del servidor y reinícialo.
- **Configuración no válida:** inspecciona los registros del servidor para conocer el motivo seguro de validación. Comprueba el esquema JSON, la ruta absoluta, la legibilidad, el propietario, el modo y cada directorio padre. No recrees un verificador válido simplemente para ocultar un problema de ruta o propiedad.
- **Se rechaza el secreto correcto:** asegúrate de que el generador y el servidor usan el mismo archivo de verificador y de que no siga ejecutándose un proceso de servidor antiguo.
- **El desbloqueo tiene éxito y se bloquea inmediatamente:** confirma que el navegador acepta cookies y que el reloj del sistema es correcto.
- **El desbloqueo funciona localmente, pero los cambios protegidos fallan a través de un proxy HTTPS:** establece `THREADCELLS_TRUSTED_PROXY_ORIGINS` en el origen HTTPS público exacto (por ejemplo, `https://threadcells.example.com`) en el entorno de servicio de ThreadCells y reinicia. No añadas rutas, comodines ni orígenes no autenticados.
- **La creación del verificador falla en un directorio no relacionado:** usa una compilación actual de ThreadCells. El comando independiente no debe inspeccionar un `.env` del directorio de trabajo.

Consulta [Modelo de seguridad](SECURITY_MODEL.md) para las suposiciones de confianza circundantes.
