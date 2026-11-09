# Oracle A1 Always Free Retry — Madrid

Automatización con GitHub Actions para intentar crear una VM Oracle Ampere A1 (VM.Standard.A1.Flex) en eu-madrid-1 cuando haya capacidad.

**Estado inicial: SEGURO.** No se ejecutan las tareas programadas hasta establecer la variable de repositorio OCI_ENABLE_LAUNCH=true. El lanzamiento manual comienza con dry_run=true, sin crear máquinas.

## Comportamiento

- Dos intentos por hora aproximadamente (17 y 47 UTC); GitHub puede retrasar o saltar cron.
- Una sola petición de creación por ejecución, sin reintentos rápidos, con forma VM.Standard.A1.Flex, 2 OCPU, 12 GB de RAM y 50 GB de disco.
- Imagen Canonical Ubuntu 24.04 ARM64; detecta dominios de disponibilidad reales y alterna si hay varios.
- Si existe una A1 no terminada en el compartimento, no crea duplicados.
- Ante errores Out of host capacity o HTTP 429, termina y espera al siguiente cron.
- Si crea o detecta la VM en un trabajo programado, intenta deshabilitar automáticamente este workflow.
- Nunca elimina, reinicia ni modifica recursos existentes.

**Costes:** los parámetros corresponden al límite nominal Always Free, pero comprueba el consumo total de recursos y volúmenes de TODOS los compartimentos: este script solo detecta duplicados en uno. No se garantiza coste cero si se han agotado cuotas. Verifica que eu-madrid-1 sea la región principal. No es necesario cambiar a Pay As You Go.

## Configurar credenciales

Ve a Settings → Secrets and variables → Actions → Secrets → New repository secret.

| Secret | Valor |
| --- | --- |
| OCI_USER_OCID | OCID del usuario API |
| OCI_TENANCY_OCID | OCID de la tenancy |
| OCI_FINGERPRINT | Huella de la API signing key |
| OCI_PRIVATE_KEY_B64 | Contenido base64 sin saltos de la clave privada API de OCI |
| OCI_COMPARTMENT_OCID | OCID del compartimento de Coolify |
| OCI_SUBNET_OCID | OCID de la subred pública de Coolify |
| OCI_SSH_PUBLIC_KEY | Contenido de una clave SSH **pública**, archivo .pub |
| OCI_PRIVATE_KEY_PASSPHRASE | Opcional si tu clave API está cifrada |

La clave privada OCI solo se guarda en GitHub Secrets; nunca en el repositorio, Issues ni chats. En Linux puedes generar el valor base64 con: base64 -w 0 ~/.oci/oci_api_key.pem. En macOS: base64 < ~/.oci/oci_api_key.pem | tr -d '\n'.

Usa una API signing key de un usuario IAM específico con permisos limitados al compartimento necesario.

## Variables

En Settings → Secrets and variables → Actions → Variables configura:
- OCI_ENABLE_LAUNCH = false inicialmente; cambiar a true solo cuando hayas validado el modo prueba y los límites de consumo.
- OCI_AVAILABILITY_DOMAIN (opcional) = vacío para detectar el AD automáticamente.
- OCI_IMAGE_OCID (opcional) = vacío para elegir imagen Ubuntu 24.04 ARM.

## Probar y activar

1. Ve a Actions → Oracle A1 Always Free - Madrid → Run workflow.
2. Deja dry_run=true. Comprueba el resumen: dry-run o already-exists.
3. Revisa región principal, cuotas y consumos de instancias y boot volumes en todos los compartimentos Oracle.
4. Cambia OCI_ENABLE_LAUNCH a true; el cron comenzará los intentos reales.
5. Tras created, comprueba que Oracle creó la VM y que GitHub Actions se deshabilitó. Si falló su permiso de desactivación, desactívalo manualmente en Actions.

Diagnóstico: configuration-error (secretos), api-error (permisos/API), no-capacity (no hay hosts), rate-limited (429), already-exists (VM presente), created (solicitud aceptada).

### Precauciones

El cron no garantiza la frecuencia exacta. En un repositorio público GitHub puede deshabilitarlo tras 60 días de inactividad. GitHub Actions usa runners estándar gratuitos para repositorios públicos. Este proyecto no incluye ninguna credencial. El permiso actions:write se usa solo para intentar detener el workflow tras el éxito.

Documentación oficial:
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- https://docs.oracle.com/en-us/iaas/tools/python/latest/api/core/client/oci.core.ComputeClient.html
