# -*- coding: utf-8 -*-
from __future__ import absolute_import

import logging

logger = logging.getLogger(__name__)

import datetime
import os
import tarfile

from ..engine import BaseEngine
from .. import utils

try:
    import docker
    from docker import errors as docker_errors
except ImportError:
    raise ImportError(u'Use of the Docker\u2122 engine requires the docker-py module.')

TEMPLATES_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        'templates'))

FILES_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        'files'))


class Engine(BaseEngine):

    # Capabilities of engine implementations
    CAP_BUILD_CONDUCTOR = True
    CAP_BUILD = True
    CAP_RUN = True
    CAP_DEPLOY = True

    display_name = u'Docker\u2122 daemon'

    _client = None

    FINGERPRINT_LABEL_KEY = 'com.ansible.container.fingerprint'
    LAYER_COMMENT = 'Built with Ansible Container (https://github.com/ansible/ansible-container)'

    @property
    def client(self):
        if not self._client:
            self._client = docker.from_env()
        return self._client

    @property
    def ansible_args(self):
        """Additional commandline arguments necessary for ansible-playbook runs."""
        return u'-c docker'

    def container_name_for_service(self, service_name):
        return u'%s_%s' % (self.project_name, service_name)

    def image_name_for_service(self, service_name):
        return u'%s-%s' % (self.project_name, service_name)

    def run_container(self,
                      image_id,
                      service_name,
                      **kwargs):
        """Run a particular container. The kwargs argument contains individual
        parameter overrides from the service definition."""


    def stop_container(self, container_id):
        try:
            container = self.client.containers.get(container_id)
        except docker_errors.APIError:
            pass
        else:
            container.stop()

    def restart_all_containers(self):
        raise NotImplementedError()

    def inspect_container(self, container_id):
        try:
            return self.client.inspect_container(container_id)
        except docker_errors.APIError:
            return None

    def delete_container(self, container_id):
        try:
            container = self.client.containers.get(container_id)
        except docker_errors.APIError:
            pass
        else:
            container.remove()

    def get_container_id_for_service(self, service_name):
        try:
            container = self.client.containers.get(self.container_name_for_service(service_name))
        except docker_errors.NotFound:
            return None
        else:
            return container.id

    def get_image_id_by_fingerprint(self, fingerprint):
        try:
            image_id, = self.client.images.list(
                all=True, quiet=True,
                filters=dict(label='%s=%s' % (self.FINGERPRINT_LABEL_KEY,
                                              fingerprint)))
        except ValueError:
            return None
        else:
            return image_id

    def get_image_id_by_tag(self, tag):
        try:
            image = self.client.images.get(tag)
            return image.id
        except docker_errors.ImageNotFound:
            return None

    def get_latest_image_id_for_service(self, service_name):
        try:
            image = self.client.images.get(
                '%s:latest' % self.image_name_for_service(service_name))
        except docker_errors.ImageNotFound:
            return None
        else:
            return image.id

    def commit_role_as_layer(self,
                             container_id,
                             service_name,
                             fingerprint,
                             metadata):
        container = self.client.containers.get(container_id)
        image_name = self.image_name_for_service(service_name)
        image_version = datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')

        image_config = utils.metadata_to_image_config(metadata)
        return container.commit(
            repository=image_name,
            tag=image_version,
            message=self.LAYER_COMMENT,
            conf=image_config
        )

    def generate_orchestration_playbook(self, repository_data=None):
        """If repository_data is specified, presume to pull images from that
        repository. If not, presume the images are already present."""
        # FIXME: Implement me.
        raise NotImplementedError()

    def push_image(self, image_id, service_name, repository_data):
        # FIXME: Implement me.
        raise NotImplementedError()

    def build_conductor_image(self, base_path, base_image):
        with utils.make_temp_dir() as temp_dir:
            logger.info('Building Docker Engine context...')
            tarball_path = os.path.join(temp_dir, 'context.tar')
            tarball_file = open(tarball_path, 'wb')
            tarball = tarfile.TarFile(fileobj=tarball_file,
                                      mode='w')
            source_dir = os.path.normpath(base_path)

            for filename in ['ansible.cfg', 'ansible-requirements.txt',
                             'requirements.yml']:
                file_path = os.path.join(source_dir, filename)
                if os.path.exists(filename):
                    tarball.add(file_path,
                                arcname=os.path.join('build-src', filename))
            # Make an empty file just to make sure the build-src dir has something
            open(os.path.join(temp_dir, '.touch'), 'w')
            tarball.add(os.path.join(temp_dir, '.touch'), arcname='build-src/.touch')

            tarball.add(os.path.join(FILES_PATH, 'get-pip.py'),
                        arcname='contrib/get-pip.py')

            tarball.add(utils.conductor_dir, arcname='conductor-src/conductor')
            tarball.add(os.path.join(os.path.dirname(utils.conductor_dir),
                                     'conductor-setup.py'),
                        arcname='conductor-src/setup.py')
            tarball.add(os.path.join(os.path.dirname(utils.conductor_dir),
                                     'conductor-requirements.txt'),
                        arcname='conductor-src/requirements.txt')

            utils.jinja_render_to_temp(TEMPLATES_PATH,
                                       'conductor-dockerfile.j2', temp_dir,
                                       'Dockerfile',
                                       conductor_base=base_image)
            tarball.add(os.path.join(temp_dir, 'Dockerfile'),
                        arcname='Dockerfile')

            #for context_file in ['builder.sh', 'ansible-container-inventory.py',
            #                     'ansible.cfg', 'wait_on_host.py', 'ac_galaxy.py']:
            #    tarball.add(os.path.join(TEMPLATES_PATH, context_file),
            #                arcname=context_file)

            logger.debug('Context manifest:')
            for tarinfo_obj in tarball.getmembers():
                logger.debug(' - %s (%s bytes)', tarinfo_obj.name, tarinfo_obj.size)
            tarball.close()
            tarball_file.close()
            tarball_file = open(tarball_path, 'rb')
            logger.info('Starting Docker build of Ansible Container Conductor image (please be patient)...')
            for line in self.client.api.build(fileobj=tarball_file,
                                              custom_context=True,
                                              tag=self.image_name_for_service('conductor'),
                                              rm=True):
                print line

"""
from logging import config
LOGGING = {
        'version': 1,
        'disable_existing_loggers': True,
        'handlers': {
            'console': {
                'level': 'DEBUG',
                'class': 'logging.StreamHandler',
            },
        },
        'loggers': {
            'container': {
                'handlers': ['console'],
                'level': 'DEBUG',
                'propagate': False
            }
        },
        'root': {
            'handlers': ['console'],
            'level': 'ERROR'
        }
    }

config.dictConfig(LOGGING)
from container.conductor import core
e = core.load_engine('docker', 'test', {})
obj = e.build_conductor_image('/Users/jginsberg/Development/ansible/ansible-container/test/layer-caching', 'centos:7')
"""
