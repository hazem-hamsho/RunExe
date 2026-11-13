class Samples:
    data = {
        "funding_dialog_dismissed": False,
        "personal_repositories": {},
    }
    default_inherited_environment = [
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "PATH",
        "PULSE_SERVER",
        "TERM",
        "TZ",
        "USER",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "XMODIFIERS",
        "XCURSOR_SIZE",
    ]
    environments = {
        "application": {
            "Runner": "wine",
            "Parameters": {"dxvk": True, "vkd3d": True, "pulseaudio_latency": False},
            "Installed_Dependencies": [
                "arial32",
                "times32",
                "courie32",
                "mono",
                "gecko",
                # "dotnet40",
                # "dotnet48"
            ],
        },
    }
