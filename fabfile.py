"""Deployment script for BIND configuration
Copyright (C) 2020  Nick M. <https://github.com/nickmasster>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import sys
import logging
import os
from os.path import dirname, basename
from pathlib import Path
from tempfile import mkdtemp

import yaml
from jinja2 import Template
from fabric import Connection
from invoke import task

# Logging configuration
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)
# stdout handler, instead of `print()` calls
stdouth = logging.StreamHandler(sys.stdout)  # pylint: disable=C0103
stdouth.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
LOGGER.addHandler(stdouth)
fh = logging.FileHandler('output.log')  # pylint: disable=C0103
fh.setLevel(logging.DEBUG)
fh.setFormatter(
    logging.Formatter('%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s'))
LOGGER.addHandler(fh)

# Verify script is running from project root directory
if os.getcwd() != dirname(__file__):
    raise RuntimeError('task must run from the project root directory')

# Load deployment configuration
try:
    with open('config.yml') as config_file:
        CONFIG = yaml.safe_load(config_file)
        BUILD_CONFIG = CONFIG.get('build', {})
        DEPLOY_CONFIG = CONFIG.get('deploy', {})
        ZONES_CONFIG = CONFIG.get('zones', {})
except IOError as err:
    LOGGER.critical('failed to load configuration file at', exc_info=True)
    raise RuntimeError from err


def __check_remote(cnx):
    """Check if context is a remote connection

    :param cnx: Current context
    :type cnx: fabric.Connection

    :raises RuntimeError: provided context is not a remote connection
    """
    if not isinstance(cnx, Connection):
        raise RuntimeError('task requires remote connection')


def __check_sudo_passwd(cnx):
    """Check if sudo password has been provided

    :param cnx: Current context
    :type cnx: fabric.Connection

    :raises RuntimeError: sudo password is missing in provided context
    """
    if not cnx.config.get('sudo', {}).get('password'):
        raise RuntimeError('task requires sudo password')


def __check_bind_installed(cnx):
    """Check if BIND installed on remote server

    :param cnx: Current context
    :type cnx: fabric.Connection

    :raises RuntimeError: BIND is not installed on remote server
    """
    __check_remote(cnx)
    if not DEPLOY_CONFIG.get('service_name'):
        raise RuntimeError('missing remote service name')
    cnx.run('dpkg -s %s' % DEPLOY_CONFIG.get('service_name'), hide=True)


def __make_updzone(zone=None, disable_root=False):
    """Create script for BIND zone update

    :param zone: Zone name
    :type zone: str

    :return: Path to created script
    :rtype: Path or None

    :raises RuntimeError: failed to create update script
    """
    zone_conf = ZONES_CONFIG.get(zone, {})
    if not zone_conf:
        raise RuntimeError('no configuration found for zone "%s"' % zone)
    zone_conf.update({'name': zone})
    script_path = Path(BUILD_CONFIG.get('output_path')).joinpath(
        'updzone_%s.sh' % zone)
    tmpl_path = Path(
        BUILD_CONFIG.get('template_path')).joinpath('updzone.sh.tmpl')
    try:
        with open(tmpl_path) as tmpl_file:
            tmpl = Template(tmpl_file.read())
    except IOError as err:
        LOGGER.error('failed to load template at %s', tmpl_path, exc_info=True)
        raise RuntimeError from err
    else:
        try:
            with open(script_path, 'w') as script_file:
                script_file.write(
                    tmpl.render(disable_root=disable_root,
                                zone=zone_conf,
                                remote=DEPLOY_CONFIG))
        except Exception as err:
            LOGGER.error('failed to build update script for zone "%s" at %s',
                         zone, script_path)
            raise RuntimeError from err
        else:
            LOGGER.debug('created updated script for zone "%s" at %s', zone,
                         script_path)
            return script_path
    return None


@task
def cmd(cnx, command):
    """Run command for BIND service on remote server

    :param cnx: Current context
    :type cnx: fabric.Connection
    :param cmd: Command
    :type cmd: str
    """
    if not DEPLOY_CONFIG.get('service_name'):
        raise RuntimeError('missing remote service name')
    __check_remote(cnx)
    command_f = 'systemctl %s %s' % (command,
                                     DEPLOY_CONFIG.get('service_name'))
    try:
        __check_sudo_passwd(cnx)
    except RuntimeError:
        res = cnx.run(command_f)
    else:
        cnx.sudo(command_f)


@task
def clean(cnx):
    """Clean build directory

    :param cnx: Current context
    :type cnx: invoke.Context
    """
    # TODO add output_path validation
    if not BUILD_CONFIG.get('output_path'):
        raise RuntimeError('missing build output path')
    LOGGER.info('cleaning build path at %s', BUILD_CONFIG.get('output_path'))
    cnx.run('rm -rf %s' % BUILD_CONFIG.get('output_path'))
    cnx.run('mkdir -p %s' % BUILD_CONFIG.get('output_path'))


@task(pre=[clean])
def build(cnx):  # pylint: disable=R0914
    """Build BIND configuration

    :param cnx: Current context
    :type cnx: invoke.Context
    """
    tmpl_path = Path(BUILD_CONFIG.get('template_path'))
    temp_path = Path(mkdtemp(prefix='bind_'))
    remote_path = Path(DEPLOY_CONFIG.get('app_path'))
    # Create temporary directory for configuration files
    LOGGER.debug('temporary path %s', temp_path)
    cnx.run('cp -R bind9/ %s' % temp_path)
    # Build zones configuration files
    rpz_zones = []
    try:
        with open(tmpl_path.joinpath('zone.tmpl')) as tmpl_file:
            tmpl = Template(tmpl_file.read())
    except IOError as err:
        LOGGER.error('failed to load template at %s', tmpl_path, exc_info=True)
        raise RuntimeError from err
    else:
        try:
            custom_zones_path = temp_path.joinpath('zones').joinpath(
                'zone.custom')
            with open(custom_zones_path, 'w') as zones_file:
                for zone_name, zone_conf in ZONES_CONFIG.items():
                    # Add zone to RPZ list
                    rpz_conf = zone_conf.get('rpz', {})
                    if rpz_conf.get('enabled', False):
                        rpz_conf.update({'name': zone_name})
                        rpz_zones.append(rpz_conf)
                        LOGGER.debug('zone "%s" added to RPZ list', zone_name)
                    # Build zone DB file
                    db_filename = 'db.%s' % zone_name
                    updzone_script = __make_updzone(zone=zone_name,
                                                    disable_root=True)
                    cnx.run('chmod +x %s' % updzone_script, hide=True)
                    # Create DB file using update script
                    cnx.run('%s -so %s' %
                            (updzone_script,
                             temp_path.joinpath('db').joinpath(db_filename)),
                            env=os.environ,
                            hide=True)
                    cnx.run('rm -f %s' % updzone_script, hide=True)
                    # Create zone config file
                    zones_file.write(
                        '%s\n' % tmpl.render(zone_name=zone_name,
                                             db_file=remote_path.joinpath(
                                                 'db').joinpath(db_filename)))
        except Exception as err:
            LOGGER.error('failed to create custom zones configuration',
                         exc_info=True)
            raise RuntimeError from err
    # Render conf.d/options.conf
    try:
        with open(tmpl_path.joinpath('options.conf.tmpl')) as tmpl_file:
            tmpl = Template(tmpl_file.read())
    except IOError as err:
        LOGGER.error('failed to load template at %s', tmpl_path, exc_info=True)
        raise RuntimeError from err
    else:
        try:
            with open(
                    temp_path.joinpath('conf.d').joinpath('options.conf'),
                    'w') as options_conf:
                options_conf.write(
                    tmpl.render(forwarders=CONFIG.get('forwarders', []),
                                rpz_zones=rpz_zones))
        except Exception as err:
            LOGGER.error('failed to create bind configuration', exc_info=True)
            raise RuntimeError from err

    # Create build tarball
    build_file = Path(BUILD_CONFIG.get('output_path')).joinpath(
        BUILD_CONFIG.get('output_file')).resolve()
    LOGGER.info('creating build file at %s', build_file)
    with cnx.cd(str(temp_path)):
        cnx.run('tar --exclude .DS_Store -czvf %s .' % build_file, hide=True)


@task(pre=[clean])
def autoupdate_on(cnx, zone=None):
    """Enable BIND zone daily auto update on remote server

    :param cnx: Current context
    :type cnx: fabric.Connection
    :param zone: Zone name
    :type zone: str
    """
    __check_remote(cnx)
    __check_sudo_passwd(cnx)
    updzone_script = __make_updzone(zone=zone, disable_root=False)
    remote_path = Path('/etc/cron.daily/bind-upd-%s' % zone)
    # Deploy auto update script
    LOGGER.info('deploying update script at %s', remote_path)
    cnx.put(updzone_script)
    cnx.sudo('mv -f %s %s' % (basename(updzone_script), remote_path),
             hide=True)
    cnx.sudo('chown root.root %s' % remote_path, hide=True)
    cnx.sudo('chmod +x %s' % remote_path, hide=True)


@task(pre=[clean])
def autoupdate_off(cnx, zone=None):
    """Disable BIND zone daily auto update on remote server

    :param cnx: Current context
    :type cnx: fabric.Connection
    :param zone: Zone name
    :type zone: str
    """
    __check_remote(cnx)
    __check_sudo_passwd(cnx)
    remote_path = Path('/etc/cron.daily/bind-upd-%s' % zone)
    LOGGER.info('removing update script at %s', remote_path)
    cnx.sudo('rm -f %s' % remote_path, hide=True)


@task(pre=[build])
def deploy(cnx):
    """Deploy BIND configuration on remote server

    :param cnx: Current context
    :type cnx: fabric.Connection
    """
    __check_remote(cnx)
    __check_sudo_passwd(cnx)
    __check_bind_installed(cnx)
    build_file = Path(BUILD_CONFIG.get('output_path')).joinpath(
        BUILD_CONFIG.get('output_file'))
    if not build_file.is_file():
        raise RuntimeError('no build for deployment')
    # Clean remote directory
    cnx.sudo('rm -f %s/named.conf*' % DEPLOY_CONFIG.get('app_path'), hide=True)
    cnx.sudo('rm -rf %s/db.*' % DEPLOY_CONFIG.get('app_path'), hide=True)
    cnx.sudo('rm -rf %s/zones*' % DEPLOY_CONFIG.get('app_path'), hide=True)
    cnx.sudo('rm -f %s/conf.d/*.conf' % DEPLOY_CONFIG.get('app_path'),
             hide=True)
    # Upload build file
    cnx.put(build_file)
    # Extract build & set permissions
    cnx.sudo('tar -xzvf %s -C %s' %
             (BUILD_CONFIG.get('output_file'), DEPLOY_CONFIG.get('app_path')),
             pty=True,
             hide=True)
    cnx.run('rm -f %s' % basename(build_file), hide=True)
    cnx.sudo('chown -R %s.%s %s' %
             (DEPLOY_CONFIG.get('user'), DEPLOY_CONFIG.get('group'),
              DEPLOY_CONFIG.get('app_path')),
             hide=True)
    # Create logs path & set permissions
    cnx.sudo('mkdir -p %s' % DEPLOY_CONFIG.get('log_path'), hide=True)
    cnx.sudo('chown -R %s.%s %s' %
             (DEPLOY_CONFIG.get('user'), DEPLOY_CONFIG.get('group'),
              DEPLOY_CONFIG.get('log_path')),
             hide=True)
    cnx.sudo('chmod -R 755 %s' % DEPLOY_CONFIG.get('log_path'), hide=True)
    # Enabled auto update for zones
    for zone_name, zone_conf in ZONES_CONFIG.items():
        if zone_conf.get('autoupdate', False):
            autoupdate_on(cnx, zone=zone_name)
    # Restart BIND9 service
    cmd(cnx, 'restart')
