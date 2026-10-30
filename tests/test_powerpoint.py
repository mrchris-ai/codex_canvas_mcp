import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from canvas_mcp import server


def presentation(extra=None, main_type=None):
    output = io.BytesIO()
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/ppt/presentation.xml" ContentType="' + (main_type or 'application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml') + '"/></Types>',
        'ppt/presentation.xml': '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>',
    }
    parts.update(extra or {})
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return output.getvalue()


class PowerPointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'Workshop notes (2026).pptx'
        self.path.write_bytes(presentation())
        self.env = mock.patch.dict(os.environ, {
            'CANVAS_POWERPOINT_UPLOAD_ROOT': str(self.root),
            'CANVAS_BASE_URL': 'https://canvas.example.edu',
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.args = {'course_id': 123, 'file_path': str(self.path),
                     'confirmation': 'APPROVE CANVAS POWERPOINT UPLOAD WRITE course 123'}

    def test_valid_package_and_frozen_bytes(self):
        path, data = server.validate_powerpoint_file(str(self.path))
        self.path.write_bytes(b'changed')
        body, _ = server.multipart_image_body({'key': 'opaque'}, path,
                                              server.POWERPOINT_CONTENT_TYPE, file_data=data)
        self.assertIn(data, body)
        self.assertNotIn(b'changed', body)

    def test_missing_or_unsafe_root(self):
        for value in ('', 'relative'):
            with mock.patch.dict(os.environ, {'CANVAS_POWERPOINT_UPLOAD_ROOT': value}):
                with self.assertRaises(server.ConfigurationError):
                    server.validate_powerpoint_file(str(self.path))
        link = self.root / 'linked-root'
        link.symlink_to(self.root, target_is_directory=True)
        with mock.patch.dict(os.environ, {'CANVAS_POWERPOINT_UPLOAD_ROOT': str(link)}):
            with self.assertRaises(server.ConfigurationError):
                server.validate_powerpoint_file(str(self.path))

    def test_paths_and_file_boundaries(self):
        outside = self.root / 'restricted'
        outside.mkdir()
        with mock.patch.dict(os.environ, {'CANVAS_POWERPOINT_UPLOAD_ROOT': str(outside)}):
            with self.assertRaises(PermissionError):
                server.validate_powerpoint_file(str(self.path))
        link = self.root / 'link.pptx'
        link.symlink_to(self.path)
        for value in (str(link), 'relative.pptx'):
            with self.assertRaises(ValueError):
                server.validate_powerpoint_file(value)
        bad = self.root / 'bad\nname.pptx'
        bad.write_bytes(presentation())
        with self.assertRaises(ValueError):
            server.validate_powerpoint_file(str(bad))
        with mock.patch.object(server, 'MAX_POWERPOINT_UPLOAD_BYTES', 10):
            with self.assertRaises(ValueError):
                server.validate_powerpoint_file(str(self.path))
        with mock.patch.object(server.os, 'getuid', return_value=-1):
            with self.assertRaises(server.ConfigurationError):
                server.validate_powerpoint_file(str(self.path))

    def test_wrong_extensions_and_disguised_files(self):
        for name in ('deck.ppt', 'deck.pptm', 'deck.zip'):
            path = self.root / name
            path.write_bytes(presentation())
            with self.assertRaises(ValueError):
                server.validate_powerpoint_file(str(path))
        for data in (b'not a zip', b'', presentation({'ppt/presentation.xml': '<wrong/>'}),
                     presentation(main_type='application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml')):
            self.path.write_bytes(data)
            with self.assertRaises(ValueError):
                server.validate_powerpoint_file(str(self.path))

    def test_active_traversal_and_xml_payloads_rejected(self):
        for extra in ({'ppt/vbaProject.bin': b'macro'}, {'../escape': b'x'},
                      {'ppt/activeX/activeX1.xml': '<x/>'}, {'payload.exe': b'executable'},
                      {'ppt/presentation.xml': '<!DOCTYPE a [<!ENTITY x "x">]><a/>'},
                      {'ppt/presentation.xml': '<!DOCTYPE a [<!ENTITY x "x">]><a/>'.encode('utf-16')},
                      {'_rels/.rels': '<Relationships/>'},
                      {'[Content_Types].xml': 'x' * (2 * 1024 * 1024 + 1)}):
            with self.subTest(extra=list(extra)):
                self.path.write_bytes(presentation(extra))
                with self.assertRaises(ValueError):
                    server.validate_powerpoint_file(str(self.path))

    def test_archive_resource_and_entry_limits(self):
        real = zipfile.ZipFile(io.BytesIO(self.path.read_bytes())).infolist()
        encrypted = zipfile.ZipInfo('encrypted.bin')
        encrypted.flag_bits = 1
        expanded = zipfile.ZipInfo('oversized.bin')
        expanded.file_size = 100 * 1024 * 1024 + 1
        symlink = zipfile.ZipInfo('symlink')
        symlink.external_attr = 0o120777 << 16
        for entries in (real + [real[0]], real * 1400, real + [encrypted],
                        real + [expanded], real + [symlink]):
            with mock.patch.object(zipfile.ZipFile, 'infolist', return_value=entries):
                with self.assertRaises(ValueError):
                    server.validate_powerpoint_file(str(self.path))

    def test_disabled_wrong_course_and_wrong_confirmation_stop_before_network(self):
        for policy, confirmation in (
            ({'enabled': False, 'approved_course_ids': [123]}, self.args['confirmation']),
            ({'enabled': True, 'approved_course_ids': [999]}, self.args['confirmation']),
            ({'enabled': True, 'approved_course_ids': [123]}, 'APPROVE CANVAS IMAGE UPLOAD course 123'),
        ):
            with mock.patch.object(server, 'write_policy', return_value=policy), mock.patch.object(server, 'request_api') as request:
                with self.assertRaises(PermissionError):
                    server.call_tool('canvas_upload_powerpoint', dict(self.args, confirmation=confirmation))
                request.assert_not_called()

    def test_three_step_flow_and_persistent_link(self):
        for status in (201, 302):
            with self.subTest(status=status), \
                 mock.patch.object(server, 'write_policy', return_value={'enabled': True, 'approved_course_ids': [123]}), \
                 mock.patch.object(server, 'request_api', side_effect=[{'upload_url': 'https://storage.example.edu/upload', 'upload_params': {'key': 'opaque'}}, {'id': 456}]) as request, \
                 mock.patch.object(server, 'post_image_payload', return_value=(status, 'https://canvas.example.edu/api/v1/files/456/create_success')) as upload, \
                 mock.patch.object(server, 'api_get', return_value={'id': 456, 'size': self.path.stat().st_size, 'content-type': server.POWERPOINT_CONTENT_TYPE}) as read:
                result = server.call_tool('canvas_upload_powerpoint', self.args)
                self.assertEqual(result['download_url'], 'https://canvas.example.edu/courses/123/files/456/download')
                self.assertEqual(request.call_args_list[0].kwargs['data']['on_duplicate'], 'rename')
                self.assertEqual(request.call_args_list[1].args[0], 'GET')
                self.assertEqual(upload.call_args.kwargs['file_data'], self.path.read_bytes())
                read.assert_called_once_with('/api/v1/courses/123/files/456')

    def test_verification_failure_and_uncertain_upload_not_retried(self):
        with mock.patch.object(server, 'require_write_approval'), \
             mock.patch.object(server, 'request_api', return_value={'upload_url': 'https://storage.example.edu/upload', 'upload_params': {'key': 'opaque'}}) as request, \
             mock.patch.object(server, 'post_image_payload', side_effect=RuntimeError('connection lost')) as upload:
            with self.assertRaises(RuntimeError):
                server.upload_canvas_powerpoint(self.args)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(upload.call_count, 1)
        with mock.patch.object(server, 'require_write_approval'), \
             mock.patch.object(server, 'request_api', side_effect=[{'upload_url': 'https://storage.example.edu/upload', 'upload_params': {'key': 'opaque'}}, {'id': 456}]), \
             mock.patch.object(server, 'post_image_payload', return_value=(201, '/api/v1/files/456')) , \
             mock.patch.object(server, 'api_get', return_value={'id': 456, 'size': 1, 'content-type': 'wrong'}):
            with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                server.upload_canvas_powerpoint(self.args)

    def test_completion_cannot_leave_canvas_and_tool_is_mutating(self):
        with mock.patch.object(server, 'request_api') as request:
            with self.assertRaises(RuntimeError):
                server.request_canvas_location('GET', 'https://evil.example.org/api/v1/files/456')
            request.assert_not_called()
        tool = next(t for t in server.TOOLS if t['name'] == 'canvas_upload_powerpoint')
        self.assertFalse(tool['annotations']['readOnlyHint'])
        self.assertTrue(tool['annotations']['destructiveHint'])
        self.assertFalse(tool['annotations']['idempotentHint'])
