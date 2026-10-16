import unittest
from unittest.mock import patch

from platform_core.common import Problem
from platform_core.external_cli import docker_executable


class DockerResolutionTests(unittest.TestCase):
    def test_path_executable_is_preferred(self):
        with patch('platform_core.external_cli.shutil.which', return_value='/configured/docker'):
            self.assertEqual(docker_executable(), '/configured/docker')

    def test_mac_minimal_ssh_path_uses_docker_desktop(self):
        with patch('platform_core.external_cli.shutil.which', return_value=None), \
             patch('platform_core.external_cli.sys.platform', 'darwin'), \
             patch('platform_core.external_cli.Path.is_file', return_value=True), \
             patch('platform_core.external_cli.os.access', return_value=True):
            self.assertEqual(docker_executable(), '/Applications/Docker.app/Contents/Resources/bin/docker')

    def test_missing_command_has_distinct_error(self):
        with patch('platform_core.external_cli.shutil.which', return_value=None), \
             patch('platform_core.external_cli.sys.platform', 'linux'):
            with self.assertRaises(Problem) as result:
                docker_executable()
            self.assertEqual(result.exception.code, 'DOCKER_CLI_NOT_FOUND')
