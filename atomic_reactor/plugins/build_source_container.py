"""
Copyright (c) 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import

import os
import subprocess
import tempfile

from atomic_reactor.build import BuildResult
from atomic_reactor.constants import (PLUGIN_SOURCE_CONTAINER_KEY, EXPORTED_SQUASHED_IMAGE_NAME,
                                      IMAGE_TYPE_OCI_TAR)
from atomic_reactor.plugin import BuildStepPlugin
from atomic_reactor.util import get_exported_image_metadata


class SourceContainerPlugin(BuildStepPlugin):
    """
    Build source container image using
    https://github.com/containers/BuildSourceImage
    """

    key = PLUGIN_SOURCE_CONTAINER_KEY

    def export_image(self, image_output_dir):
        output_path = os.path.join(tempfile.mkdtemp(), EXPORTED_SQUASHED_IMAGE_NAME)

        cmd = ['skopeo', 'copy']
        source_img = 'oci:{}'.format(image_output_dir)
        dest_img = 'oci-archive:{}'.format(output_path)
        cmd += [source_img, dest_img]

        self.log.info("Calling: %s", ' '.join(cmd))
        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            self.log.error("failed to save oci-archive :\n%s", e.output)
            raise

        img_metadata = get_exported_image_metadata(output_path, IMAGE_TYPE_OCI_TAR)
        self.workflow.exported_image_sequence.append(img_metadata)

    def run(self):
        """Build image inside current environment.

        Returns:
            BuildResult
        """
        source_data_dir = tempfile.mkdtemp()  # TODO: from pre_* plugin
        # TODO fail when source dir is empty

        image_output_dir = tempfile.mkdtemp()

        cmd = ['bsi',
               '-d',
               'sourcedriver_rpm_dir',
               '-s',
               '{}'.format(source_data_dir),
               '-o',
               '{}'.format(image_output_dir)]

        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            self.log.error("BSI failed with output:\n%s", e.output)
            return BuildResult(logs=e.output, fail_reason='BSI utility failed build source image')

        self.log.debug("Build log:\n%s\n", output)

        self.export_image(image_output_dir)

        return BuildResult(
            logs=output,
            oci_image_path=image_output_dir,
            skip_layer_squash=True
        )
