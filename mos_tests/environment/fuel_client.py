#    Copyright 2015 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import logging
import os

from fuelclient import client
from fuelclient import fuelclient_settings
from fuelclient.objects.environment import Environment as EnvironmentBase
from paramiko import RSAKey
from waiting import wait

from mos_tests.environment.ssh import SSHClient


logger = logging.getLogger(__name__)


class NodeProxy(object):
    """Fuelclient Node proxy model with some helpful methods"""

    def __init__(self, orig_node, env):
        self._orig_node = orig_node
        self._env = env

    def __getattr__(self, name):
        return getattr(self._orig_node, name)

    @property
    def ip_list(self):
        """Returns node ip addresses list"""
        return [x['ip'].split('/')[0] for x in self.data['network_data']
                if 'ip' in x]

    def ssh(self):
        return SSHClient(
            host=self.data['ip'],
            username='root',
            private_keys=self._env.admin_ssh_keys
        )


class Environment(EnvironmentBase):
    """Extended fuelclient Environment model with some helpful methods"""

    admin_ssh_keys = None

    def get_all_nodes(self):
        nodes = super(Environment, self).get_all_nodes()
        return [NodeProxy(x, self) for x in nodes]

    def get_primary_controller_ip(self):
        """Return public ip of primary controller"""
        return self.get_network_data()['public_vip']

    def find_node_by_fqdn(self, fqdn):
        """Returns list of fuelclient.objects.Node instances for cluster"""
        for node in self.get_all_nodes():
            if node.data['fqdn'] == fqdn:
                return node
        raise Exception("Node doesn't found")

    def get_ssh_to_node(self, ip):
        return SSHClient(
            host=ip,
            username='root',
            private_keys=self.admin_ssh_keys
        )

    def get_ssh_to_vm(self, ip, username=None, password=None,
                      private_keys=None):
        return SSHClient(
            host=ip, username=username, password=password,
            private_keys=private_keys)

    def get_nodes_by_role(self, role):
        """Returns nodes by assigned role"""
        return [x for x in self.get_all_nodes()
                if role in x.data['roles']]

    def is_ostf_tests_pass(self):
        """Check for OpenStack tests pass"""

        def test_is_done():
            result = self.get_state_of_tests()[0]
            if result['status'] == 'finished':
                return result

        logger.info('[Re]start OSTF tests')
        if self.is_ha:
            self.run_test_sets(['ha'])
        else:
            self.run_test_sets(['sanity'])
        result = wait(test_is_done, timeout_seconds=10 * 60)
        for test in result['tests']:
            if test['status'] != 'success':
                logger.warning(
                    'Test "{name}" status is {status}; {message}'.format(
                        **test))
                return False
        return True

    @property
    def is_operational(self):
        return self.status == 'operational'

    @property
    def is_ha(self):
        return self.data['mode'] == 'ha_compact'

    @property
    def network_segmentation_type(self):
        return self.get_network_data()[
            'networking_parameters']['segmentation_type']

    @property
    def certificate(self):
        ssl = self.get_settings_data()['editable']['public_ssl']
        if ssl['services']['value']:
            return ssl['cert_data']['value']['content']

    @property
    def leader_controller(self):
        controllers = self.get_nodes_by_role('controller')
        controller_ip = controllers[0].data['ip']
        with self.get_ssh_to_node(controller_ip) as remote:
            response = remote.execute('pcs status cluster')
        stdout = ' '.join(response['stdout'])
        for controller in controllers:
            if controller.data['fqdn'] in stdout:
                return controller

    @property
    def primary_controller(self):
        controllers = self.get_nodes_by_role('controller')
        for controller in controllers:
            ip = controller.data['ip']
            with self.get_ssh_to_node(ip) as remote:
                response = remote.execute('hiera role')
                stdout = ' '.join(response['stdout'])
                if 'primary-controller' in stdout:
                    return controller

    def destroy_nodes(self, devops_nodes):
        logger.info('wait until the nodes get offline state')
        node_ips = [node.get_ip_address_by_network_name('admin')
                    for node in devops_nodes]
        for node in devops_nodes:
            node.destroy()
        wait(lambda: self.check_nodes_get_offline_state(node_ips),
             timeout_seconds=10 * 60)
        for node in self.get_all_nodes():
            logger.info('online state of node {0} now is {1}'
                        .format(node.data['name'], node.data['online']))

    def warm_shutdown_nodes(self, devops_nodes):
        for node in devops_nodes:
            node_ip = node.get_ip_address_by_network_name('admin')
            logger.info('Shutdown node {0} with ip {1}'
                        .format(node.name, node_ip))
            with self.get_ssh_to_node(node_ip) as remote:
                remote.check_call('/sbin/shutdown -Ph now')
        self.destroy_nodes(devops_nodes)

    def warm_start_nodes(self, devops_nodes):
        for node in devops_nodes:
            logger.info('Starting node {}'.format(node.name))
            node.create()
        wait(self.check_nodes_get_online_state, timeout_seconds=10 * 60)
        logger.info('wait until the nodes get online state')
        for node in self.get_all_nodes():
            logger.info('online state of node {0} now is {1}'
                        .format(node.data['name'], node.data['online']))

    def warm_restart_nodes(self, devops_nodes):
        logger.info('Reboot (warm restart) nodes %s',
                    [n.name for n in devops_nodes])
        self.warm_shutdown_nodes(devops_nodes)
        self.warm_start_nodes(devops_nodes)

    def check_nodes_get_offline_state(self, node_ips=()):
        nodes_states = [not x.data['online']
                        for x in self.get_all_nodes()
                        if x.data['ip'] in node_ips]
        return all(nodes_states)

    def check_nodes_get_online_state(self):
        return all([node.data['online'] for node in self.get_all_nodes()])

    def get_node_ip_by_host_name(self, hostname):
        controller_ip = ''
        for node in self.get_all_nodes():
            if node.data['fqdn'] == hostname:
                controller_ip = node.data['ip']
                break
        return controller_ip


class FuelClient(object):
    """Fuel API client"""
    def __init__(self, ip, login, password, ssh_login, ssh_password):
        logger.debug('Init fuel client on {0}'.format(ip))
        self.reconfigure_fuelclient(ip, login, password)
        self.admin_ip = ip
        self.ssh_login = ssh_login
        self.ssh_password = ssh_password
        self._admin_keys = None

    @staticmethod
    def reconfigure_fuelclient(ip, login, password):
        """There is ugly way to reconfigure fuelclient APIClient singleton"""
        os.environ.update({
            'SERVER_ADDRESS': ip,
            'KEYSTONE_USER': login,
            'KEYSTONE_PASS': password,
        })
        fuelclient_settings._SETTINGS = None
        client.APIClient.__init__()

    def get_last_created_cluster(self):
        """Returns Environment instance for latest deployed cluster"""
        env = Environment.get_all()[-1]
        env.admin_ssh_keys = self.admin_keys
        return env

    @property
    def admin_keys(self):
        """Return list with private ssh keys from Fuel master node"""
        if self._admin_keys is None:
            self._admin_keys = []
            with SSHClient(host=self.admin_ip,
                           username=self.ssh_login,
                           password=self.ssh_password) as remote:
                for path in ['/root/.ssh/id_rsa',
                             '/root/.ssh/bootstrap.rsa']:
                    with remote.open(path) as f:
                        self._admin_keys.append(RSAKey.from_private_key(f))
        return self._admin_keys
