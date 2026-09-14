import re


def test_grub_config_transform():
    sample_grub = (
        'GRUB_DEFAULT=0\n'
        'GRUB_TIMEOUT=5\n'
        'GRUB_TIMEOUT_STYLE=menu\n'
        'GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"\n'
    )

    # Apply same transformations as fix_omni10.sh
    if "GRUB_RECORDFAIL_TIMEOUT" not in sample_grub:
        sample_grub += "GRUB_RECORDFAIL_TIMEOUT=0\n"
    else:
        sample_grub = re.sub(r"^GRUB_RECORDFAIL_TIMEOUT=.*", "GRUB_RECORDFAIL_TIMEOUT=0", sample_grub, flags=re.M)

    sample_grub = re.sub(r"^GRUB_TIMEOUT=.*", "GRUB_TIMEOUT=0", sample_grub, flags=re.M)
    sample_grub = re.sub(r"^GRUB_TIMEOUT_STYLE=.*", "GRUB_TIMEOUT_STYLE=hidden", sample_grub, flags=re.M)

    assert "GRUB_TIMEOUT=0" in sample_grub
    assert "GRUB_TIMEOUT_STYLE=hidden" in sample_grub
    assert "GRUB_RECORDFAIL_TIMEOUT=0" in sample_grub


def test_grub_10_linux_ramdisk_silencing():
    sample_10_linux = (
        '  message="$(gettext_printf "Loading Linux %s ..." ${version})"\n'
        '  sed "s/^/$submenu_indentation/" << EOF\n'
        '        echo    \'$(echo "$message" | grub_quote)\'\n'
        '        linux   ${rel_dirname}/${basename} root=${linux_root_device_thisversion} rw ${args}\n'
        'EOF\n'
        '  if test -n "${initrd}" ; then\n'
        '    message="$(gettext_printf "Loading initial ramdisk ...")"\n'
        '    sed "s/^/$submenu_indentation/" << EOF\n'
        '        echo    \'$(echo "$message" | grub_quote)\'\n'
        '        initrd  $(echo $initrd_path)\n'
        'EOF\n'
        '  fi\n'
    )

    # Perform transformation: comment out echo lines
    transformed = re.sub(
        r'^[ \t]*echo[ \t]*\'\$\(echo "\$message" \| grub_quote\)\'',
        '        # echo \'$(echo "$message" | grub_quote)\'',
        sample_10_linux,
        flags=re.M
    )

    assert "echo    '$(echo \"$message\" | grub_quote)'" not in transformed
    assert "# echo '$(echo \"$message\" | grub_quote)'" in transformed
