"""
Copyright (c) 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import

import subprocess
import tempfile

from atomic_reactor.build import BuildResult, ImageName
from atomic_reactor.constants import PLUGIN_SOURCE_CONTAINER_KEY
from atomic_reactor.plugin import BuildStepPlugin
from atomic_reactor.plugins.pre_reactor_config import get_value


class SourceContainerPlugin(BuildStepPlugin):
    """
    Build source container image using
    https://github.com/containers/BuildSourceImage

    Image https://quay.io/repository/ctrs/bsi should be pushed to image stream
    on OCP instance and image name must be specified in config
    option `source_builder_image`
    """

    key = PLUGIN_SOURCE_CONTAINER_KEY

    def get_builder_image(self):
        source_containers_conf = get_value(self.workflow, 'source_containers', {})
        return source_containers_conf.get('source_builder_image')

    def run(self):
        """Build image inside current environment.
        It's expected this may run within (privileged) docker container.

        Returns:
            BuildResult
        """
        source_data_dir = tempfile.mkdtemp()  # TODO: from pre_* plugin
        # TODO fail when source dir is empty

        image_output_dir = tempfile.mkdtemp()
        image = self.get_builder_image()
        if not image:
            raise RuntimeError(
                'Cannot build source containers, builder image is not '
                'specified in configuration')

        image = ImageName.parse(image)

        srpms_path = '/data/'
        output_path = '/output/'

        # "podman run -it -v $(pwd)/output/:/output/ -v $(pwd)/SRCRPMS/:/data/ -u $(id -u) quay.io/ctrs/bsi -s /data/ -o /output/"
        # "podman run -it -v {image_output_dir}:{output_path} -v {source_data_dir}:{srpms_path} -u $(id -u) {image} -s {srpms_path} -o {output_path}"
        cmd = ['podman',
               '--storage-driver=vfs',
               'run',
               '-it',
               '-v',
               '{}:{}'.format(image_output_dir, output_path),
               '-v',
               '{}:{}'.format(source_data_dir, srpms_path),
               '{}'.format(image),
               '-s',
               '{}'.format(srpms_path),
               '-o',
               '{}'.format(output_path)]

        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            self.log.error("build failed with output:\n%s", e.output)
            return BuildResult(logs=e.output, fail_reason='source container build failed')

        self.log.debug("Build log:\n%s", "\n".join(output))

        return BuildResult(
            logs=output,
            oci_image_path=image_output_dir,
            skip_layer_squash=True
        )
