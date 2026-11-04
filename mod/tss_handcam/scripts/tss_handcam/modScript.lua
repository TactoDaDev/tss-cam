-- TSS Handcam: load the game-engine side when the mod is mounted (core_modmanager runs every
-- /scripts/*/modScript.lua). "manual" unload mode, set before the load, keeps it across level changes.
setExtensionUnloadMode("tssHandcam", "manual")
extensions.load("tssHandcam")
