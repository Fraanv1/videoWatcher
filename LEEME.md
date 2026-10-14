# videoWatcher: guía en español

## Versión portable (para una PC sin nada instalado)

Copiá el archivo **`dist\videoWatcher_portable.zip`** (30 MB) a la otra PC con un pendrive y
descomprimilo, por ejemplo en el Escritorio. Dentro de la carpeta `videoWatcher`:

- **Doble clic en `videoWatcher.exe`** abre un menú:
  1. *Abrir videoWatcher*: abre el navegador para buscar y descargar por red local. La primera vez te pide crear un usuario.
  2. *Convertir videos de la tarjeta microSD a MP4*: arrastrá la carpeta de la tarjeta a la ventana.
     Los MP4 y `hashes_sha256.csv` quedan en el Escritorio.
- **Atajo:** arrastrá la carpeta de la tarjeta encima de `videoWatcher.exe`.
- No hace falta Python, ffmpeg ni conexión a Internet: todo viene incluido.
- Si aparece "Windows protegió su PC", tocá *Más información* y después *Ejecutar de todas formas*
  (el programa no tiene firma digital).

Funciona en Windows 10/11 de 64 bits. Para volver a armar el paquete en esta PC:
`powershell -ExecutionPolicy Bypass -File build_portable.ps1`

## ⚠️ Si pasó algo grave: cómo conservar los videos

**Las cámaras sobrescriben las grabaciones viejas** cuando la tarjeta microSD se llena
(configuración `OverWrite` confirmada en el log de VMS Pro). Cada archivo de 5 minutos pesa
~58 MB, o sea unos **17 GB por día por cámara**. Con una tarjeta de 32 GB quedan unos 2 días;
con una de 64 GB, unos 3 o 4. Si el hecho que necesitás ver tiene más días, puede que ya no esté.

Ordené las opciones de la más a la menos confiable:

### Opción 1 (la mejor): sacar la tarjeta microSD

1. Andá adonde está la cámara y **sacale la microSD** (conviene desenchufar la cámara antes).
   Así deja de grabar encima y conservás **todas** las grabaciones originales, en calidad completa.
2. Hacé una **copia completa** de la tarjeta a tu PC con un lector de tarjetas
   (por ejemplo a `C:\Evidencia\camara1\`). No borres ni edites nada de la tarjeta.
   Si Windows no muestra la tarjeta o pide formatearla, **no la formatees**: puede usar un formato que
   Windows no lee. En ese caso guardala y consultá a un técnico o a la policía.
3. Convertí la copia a MP4 (la tarjeta original no se toca):

   ```powershell
   cd C:\Users\Fraan\Downloads\Icsee\videoWatcher
   $env:VIDEOWATCHER_FFMPEG = "C:\Users\Fraan\Downloads\Icsee\General_Beta_VMS Pro_Win64\General_Beta_VMS Pro_Win64\ffmpeg.exe"
   python -m videowatcher convert C:\Evidencia\camara1 C:\Evidencia\camara1_mp4
   ```

   Se crean los `.mp4` y el archivo `hashes_sha256.csv`, que tiene la huella digital SHA-256 de
   cada original. Sirve para demostrar después que las copias son idénticas a la tarjeta.
4. Guardá la tarjeta en un lugar seguro. Si hacés una denuncia, la policía o el juzgado puede
   pedirla.

### Opción 2: ir con la notebook a la red de la cámara

La cámara `5e85c8919c2b4f5b2pya` está en **otra red**: IP local `192.168.1.33`, IP pública
`181.24.30.173`. Tu PC está en `192.168.100.x`. Si llevás una notebook y la conectás al Wi-Fi de
la cámara, videoWatcher descarga directo por IP, sin pasar por la nube (ver "Uso" más abajo).

### Opción 3 (desde lejos, ahora mismo): grabar la reproducción

La **reproducción** sí funciona por la nube; lo que se corta es la **descarga**. Como medida
urgente, mientras conseguís la tarjeta:

1. En VMS Pro, abrí **Reproducción**, elegí la cámara, la fecha y la hora del hecho.
2. Grabá la pantalla mientras se reproduce: **Win + Alt + R** (Xbox Game Bar), o la
   *Herramienta Recortes* en modo video (el ícono de cámara de video).
3. Anotá fecha, hora y cámara de cada grabación.

La calidad es menor que el original, pero asegura una copia ya.

### Por qué la descarga de VMS Pro no funciona

En el log de VMS Pro (`Log\log\netsdk.log`, 18:36–18:37 y 18:43–18:45) se ve esto:

- La descarga viaja por un servidor intermedio de Xiongmai (`23.251.39.39`), porque la cámara y tu
  PC están en redes distintas.
- Durante la transferencia aparecen errores **`Stream Data Loss`**, y después la cámara se desconecta (`-1007`).
- Aun así, VMS Pro marca **"Descarga completada"** al 100%. **Es un falso positivo.** El archivo
  quedó vacío y después desapareció de la carpeta `downloads` (18:45:55, cuando falló el reintento).

Reintentar desde VMS Pro probablemente vuelva a fallar. Las opciones 1 y 2 evitan la nube por completo.

---

## Uso de videoWatcher (descarga por red local)

### Requisitos

- Python 3.11 o superior: `pip install -r requirements.txt`
- La PC tiene que poder llegar a la IP de la cámara, puerto TCP **34567** (misma red, VPN o
  redirección de puertos).
- Para MP4: ffmpeg. Podés usar el que viene con VMS Pro (variable `VIDEOWATCHER_FFMPEG`, como arriba).

### Primer uso

```powershell
cd C:\Users\Fraan\Downloads\Icsee\videoWatcher
pip install -r requirements.txt
python -m videowatcher adduser fran        # crea tu usuario de la app (mínimo 8 caracteres)
python -m videowatcher serve               # abrí http://127.0.0.1:8765
```

1. Iniciá sesión con el usuario que creaste.
2. Tocá **+ Add**. Poné un nombre, la **IP** de la cámara (ej. `192.168.1.33`), el puerto `34567`,
   y el usuario y la contraseña de la cámara.
3. **Test connection** comprueba que llega a la cámara.
4. Elegí la **fecha** y el rango de horas, y tocá **Search**.
5. Marcá los archivos y tocá **Download selected**. Con **Convert to MP4** activado también te deja un `.mp4`.
6. Los archivos quedan en `downloads\<cámara>\<fecha>\`. Siempre se guarda el `.h264` original.

### Usuario y contraseña de la cámara

iCSee puede haber cambiado `admin` por un usuario al azar. En la base de VMS Pro aparecen usuarios
como `fxcu` o `dmth`. Probá primero `admin` con la contraseña que pusiste en iCSee.

### Seguridad

- La app solo escucha en tu PC (`127.0.0.1`).
- Tu contraseña de la app se guarda cifrada (scrypt). Tras 5 intentos fallidos se bloquea 5 minutos.
- Las contraseñas de las cámaras quedan **en texto plano** en `data\cameras.json` (el protocolo de
  la cámara las necesita). No compartas la carpeta `data`.

## Recomendaciones si es para una denuncia

- No edites ni recortes los originales. Trabajá sobre copias.
- Guardá `hashes_sha256.csv` junto con los videos.
- Anotá cuándo y cómo obtuviste cada archivo: quién sacó la tarjeta, a qué hora y en qué PC se copió.
- La hora que muestran los videos es la del reloj de la cámara. VMS Pro la sincroniza con la PC
  (`AutoSyncSystemTime=true`), pero conviene comparar con un reloj confiable.
