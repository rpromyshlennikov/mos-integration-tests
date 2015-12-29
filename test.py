def main():
    print 'asdfdfds'
    print 'asdfdfds'
    print 'asdfdfds'
    print 'asdfdfds'


if __name__ == '__main__':
    main()


u"""
что делают остф тесты (прошёл ручками, чтобы убедиться):
1) hiera amqp_hosts отдаёт в моём случае ip нод с раббитом,
но код берет 1ую ноду (10.109.16.8:5673 - node-5)
2) rabbitmqctl cluster_status  - висит, она убивает!!!
раббит на ноде, на которой выполняется, то есть на node-5
3) """

u"""
hiera amqp_hosts
rabbitmqctl cluster_status - вешается и убивает? раббит на том контроллере,
на котором выполняется эта команда.
crm resource status master_p_rabbitmq-server


1. pcs resource disable p_rabbitmq-server
2. дождаться когда все ляжет

root@node-1:~# crm resource status master_p_rabbitmq-server
resource master_p_rabbitmq-server is running on: node-1.test.domain.local
resource master_p_rabbitmq-server is running on: node-3.test.domain.local
resource master_p_rabbitmq-server is running on: node-5.test.domain.local
root@node-1:~# crm resource status master_p_rabbitmq-server
resource master_p_rabbitmq-server is running on: node-5.test.domain.local
resource master_p_rabbitmq-server is NOT running
resource master_p_rabbitmq-server is NOT running
root@node-1:~# crm resource status master_p_rabbitmq-server
resource master_p_rabbitmq-server is running on: node-5.test.domain.local
resource master_p_rabbitmq-server is NOT running
resource master_p_rabbitmq-server is NOT running
root@node-1:~# crm resource status master_p_rabbitmq-server
resource master_p_rabbitmq-server is running on: node-5.test.domain.local
resource master_p_rabbitmq-server is NOT running
resource master_p_rabbitmq-server is NOT running
root@node-1:~# crm resource status master_p_rabbitmq-server
resource master_p_rabbitmq-server is running on: node-5.test.domain.local
resource master_p_rabbitmq-server is NOT running
resource master_p_rabbitmq-server is NOT running
root@node-1:~# crm resource status master_p_rabbitmq-server
resource master_p_rabbitmq-server is NOT running
resource master_p_rabbitmq-server is NOT running
resource master_p_rabbitmq-server is NOT running
root@node-1:~# crm resource status master_p_rabbitmq-server
resource master_p_rabbitmq-server is NOT running
resource master_p_rabbitmq-server is NOT running
resource master_p_rabbitmq-server is NOT running


crm resource status master_p_rabbitmq-server 2>&1 |
grep 'is NOT running' | wc -l  == 0 on working, and > 0 on unworking

crm resource status master_p_rabbitmq-server 2>&1 | wc -l  == 3
3
"""

# from mos_tests.environment.fuel_client.Environment#is_ostf_tests_pass:


def reset_rabbit(self):
    # TODO: remove this debug
    import pydevd
    pydevd.settrace('172.16.68.146', port=20001,
                    stdoutToServer=True, stderrToServer=True)
    controllers = self.get_nodes_by_role('controller')

    with controllers[0].ssh() as remote:
        cmd_get_count = (
            'crm resource status master_p_rabbitmq-server 2>&1 | wc -l')
        cluster_len = int(
            remote.execute(cmd_get_count)['stdout'][0].strip())
        cmd_disable = (
            'pcs resource disable p_rabbitmq-server')
        remote.execute(cmd_disable)
        cmd_count_disabled = (
            "crm resource status master_p_rabbitmq-server 2>&1 | "
            "grep 'is NOT running' | wc -l")
        wait(lambda:
             int(remote.execute(cmd_count_disabled)['stdout'][0].strip()
                 ) == cluster_len,
             timeout_seconds=10 * 60, sleep_seconds=10)

    for controller in controllers:
        with controller.ssh() as remote:
            cmd_kill_beam = 'killall beam.smp'
            res = remote.execute(cmd_kill_beam)
            logger.log(res)

    with controllers[0].ssh() as remote:
        cmd_enable = (
            'pcs resource enable p_rabbitmq-server')
        remote.execute(cmd_enable)
        wait(lambda:
             int(remote.execute(cmd_count_disabled)['stdout'][0].strip()
                 ) == 0,
             timeout_seconds=10 * 60, sleep_seconds=10)
    from time import sleep
    sleep(30)

def workaround_for_rabbit():
    # TODO (rpromyshlennikov): remove this WA after next bug-fixes (
    # https://bugs.launchpad.net/fuel/+bug/1524024
    # https://bugs.launchpad.net/mos/+bug/1529602
    # https://bugs.launchpad.net/fuel/+bug/1529230
    # https://bugs.launchpad.net/fuel/+bug/1529121)
    self.run_test_sets(['ha'])
    results = wait(test_is_done, timeout_seconds=10 * 60)
    tests_error = any(
        [res['status'] == 'error' for res in results['tests']])
    if tests_error:
        self.reset_rabbit()

# TODO: remove this workaround after bug-fixes for (
    # https://bugs.launchpad.net/fuel/+bug/1524024
    # https://bugs.launchpad.net/mos/+bug/1529602
    # https://bugs.launchpad.net/fuel/+bug/1529230
    # https://bugs.launchpad.net/fuel/+bug/1529121)
    workaround_for_rabbit()


# from mos_tests.environment.devops_client.DevopsClient#revert_snapshot:
cls.restore_cluster(env)


@classmethod
def restore_cluster(cls, env):
    with env.get_admin_remote() as remote:
        out = remote.execute(
            "for i in $("
                "fuel node | grep controller | awk '{print $1}'"
            "); do (ssh node-$i '"
                'killall beam.smp; '
                'crm resource restart p_rabbitmq-server'
            "') done")
        logger.debug(out)
