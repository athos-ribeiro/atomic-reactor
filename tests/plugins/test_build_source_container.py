"""
Copyright (c) 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import unicode_literals, absolute_import

import os
import subprocess
import tempfile

from flexmock import flexmock
import pytest
import json
import tarfile

from atomic_reactor.inner import DockerBuildWorkflow
from atomic_reactor.constants import EXPORTED_SQUASHED_IMAGE_NAME
from atomic_reactor.core import DockerTasker
from atomic_reactor.plugin import BuildStepPluginsRunner, PluginFailedException
from atomic_reactor.plugins.build_source_container import SourceContainerPlugin
from atomic_reactor.plugins.pre_reactor_config import (
    ReactorConfigPlugin,
)
from tests.docker_mock import mock_docker
from tests.constants import TEST_IMAGE, MOCK_SOURCE


class MockSource(object):

    def __init__(self, tmpdir):
        tmpdir = str(tmpdir)
        self.dockerfile_path = os.path.join(tmpdir, 'Dockerfile')
        self.path = tmpdir
        self.config = flexmock(image_build_method=None)

    def get_build_file_path(self):
        return self.dockerfile_path, self.path


class MockInsideBuilder(object):

    def __init__(self):
        mock_docker()
        self.tasker = DockerTasker()
        self.base_image = None
        self.image_id = None
        self.image = None
        self.df_path = None
        self.df_dir = None
        self.parent_images_digests = {}

    def ensure_not_built(self):
        pass


def mock_workflow(tmpdir):
    workflow = DockerBuildWorkflow(TEST_IMAGE, source=MOCK_SOURCE)
    builder = MockInsideBuilder()
    source = MockSource(tmpdir)
    setattr(builder, 'source', source)
    setattr(workflow, 'source', source)
    setattr(workflow, 'builder', builder)

    workflow.plugin_workspace[ReactorConfigPlugin.key] = {}

    return workflow


@pytest.mark.parametrize('export_failed', (True, False))
def test_running_build(tmpdir, caplog, export_failed):
    """
    Test if proper result is returned and if plugin works
    """
    workflow = mock_workflow(tmpdir)
    mocked_tasker = flexmock(workflow.builder.tasker)
    mocked_tasker.should_receive('wait').and_return(0)
    runner = BuildStepPluginsRunner(
        mocked_tasker,
        workflow,
        [{
            'name': SourceContainerPlugin.key,
            'args': {},
        }]
    )

    temp_source_dir = os.path.join(str(tmpdir), 'source_dir')
    temp_image_output_dir = os.path.join(str(tmpdir), 'image_output_dir')
    temp_image_export_dir = os.path.join(str(tmpdir), 'image_export_dir')
    tempfile_chain = flexmock(tempfile).should_receive("mkdtemp").and_return(temp_source_dir)
    tempfile_chain.and_return(temp_image_output_dir)
    tempfile_chain.and_return(temp_image_export_dir)
    os.mkdir(temp_source_dir)
    os.mkdir(temp_image_export_dir)
    os.makedirs(os.path.join(temp_image_output_dir, 'blobs', 'sha256'))

    def check_check_output(args, **kwargs):
        if args[0] == 'skopeo':
            assert args[0] == 'skopeo'
            assert args[1] == 'copy'
            assert args[2] == 'oci:%s' % temp_image_output_dir
            assert args[3] == 'oci-archive:%s' % os.path.join(temp_image_export_dir,
                                                              EXPORTED_SQUASHED_IMAGE_NAME)

            if export_failed:
                raise subprocess.CalledProcessError(returncode=1, cmd=args, output="Failed")

            return ''
        else:
            assert args[0] == 'bsi'
            assert args[1] == '-d'
            assert args[2] == 'sourcedriver_rpm_dir'
            assert args[3] == '-s'
            assert args[4] == temp_source_dir
            assert args[5] == '-o'
            assert args[6] == temp_image_output_dir
            return 'stub stdout'

    (flexmock(subprocess)
     .should_receive("check_output")
     .times(2)
     .replace_with(check_check_output))

    blob_sha = "f568c411849e21aa3917973f1c5b120f6b52fe69b1944dfb977bc11bed6fbb6d"
    index_json = {"schemaVersion": 2,
                  "manifests":
                      [{"mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": "sha256:%s" % blob_sha,
                        "size": 645,
                        "annotations": {"org.opencontainers.image.ref.name": "latest-source"},
                        "platform": {"architecture": "amd64", "os": "linux"}}]}
    blob_json = {"schemaVersion": 2, "layers": []}

    with open(os.path.join(temp_image_output_dir, 'index.json'), 'w') as fp:
        fp.write(json.dumps(index_json))
    with open(os.path.join(temp_image_output_dir, 'blobs', 'sha256', blob_sha), 'w') as fp:
        fp.write(json.dumps(blob_json))

    if not export_failed:
        export_tar = os.path.join(temp_image_export_dir, EXPORTED_SQUASHED_IMAGE_NAME)
        with open(export_tar, "wb") as f:
            with tarfile.TarFile(mode="w", fileobj=f) as tf:
                for f in os.listdir(temp_image_output_dir):
                    tf.add(os.path.join(temp_image_output_dir, f), f)

    if export_failed:
        with pytest.raises(PluginFailedException):
            runner.run()
    else:
        build_result = runner.run()
        assert not build_result.is_failed()
        assert build_result.oci_image_path
        assert 'stub stdout' in caplog.text


def test_failed_build(tmpdir, caplog):
    """
    Test if proper error state is returned when build inside build
    container failed
    """
    (flexmock(subprocess).should_receive('check_output')
     .and_raise(subprocess.CalledProcessError(1, 'cmd', output='stub stdout')))
    workflow = mock_workflow(tmpdir)
    mocked_tasker = flexmock(workflow.builder.tasker)
    mocked_tasker.should_receive('wait').and_return(1)
    runner = BuildStepPluginsRunner(
        mocked_tasker,
        workflow,
        [{
            'name': SourceContainerPlugin.key,
            'args': {},
        }]
    )

    build_result = runner.run()
    assert build_result.is_failed()
    assert 'BSI failed with output:' in caplog.text
    assert 'stub stdout' in caplog.text
