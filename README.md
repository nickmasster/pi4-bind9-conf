# pi4-bind9-conf

## Description

Project contains configuration for [BIND 9](https://gitlab.isc.org/isc-projects/bind9) (mirror on [github](https://github.com/isc-projects/bind9)), including deployment script and **ads blocking** configuration.

Optimized for Raspberry Pi4 running Raspbian. Contains default configuration for tld `home`.

Project can be extended for different, automatically updated, DNS zones. Not only for ads blocking purpose.

## Installation

```pip install -r requirements.txt```

## Configuration

For BIND 9 configuration see [BIND 9 Administrator Reference Manual](https://github.com/isc-projects/bind9/blob/master/doc/arm/Bv9ARM.pdf).

This repository contains basic configuration, optimized for small home network, located in [bind9/](bind9).

## Ads Blocking

Project contains script for creating BIND zone file with ads-related domains in order to block requests to these domains on DNS level.

Instead of resolving IP address of such domain, [NXDOMAIN](https://tools.ietf.org/html/rfc8020) response will be returned.

Implemenation based on [RPZ (Response Policy Zones)](https://www.isc.org/rpz) added in BIND version 9.

List of URLs contains domains list for RPZ can be found in [config.json](config.json).

## Manage and Deploy

Deployment implemented using [Fabric](https://www.fabfile.org) framework and requires `root` permissions on a remote server. You will be asked for password in order to run `sudo` command.

If you are using `root` user for deployment (not recommmended) or `sudo` configuration on your server does not require password (not recommended) - you can ommit `--prompt-for-sudo-password` argument for following commands.

### Deploy

```fab -H <hostname> --prompt-for-sudo-password deploy```

### Autoupdate

After deployment of basic configuration, you can enable or disable automatic daily updates of specified DNS zones.

#### Enable

```fab -H <hostname> --prompt-for-sudo-password autoupdate-on --zone <zonename>```

#### Disable

```fab -H <hostname> --prompt-for-sudo-password autoupdate-off --zone <zonename>```

## License

See [LICENSE](LICENSE) file for more details.
