# Copyright (c) 2012 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

import os
from calendar import timegm
from datetime import datetime
import getpass
import unittest2
import luigi
from luigi import hdfs
import mock
import re
import functools
from nose.plugins.attrib import attr
from snakebite.minicluster import MiniCluster


class TestException(Exception):
    pass


@attr('minicluster')
class HdfsTestCase(unittest2.TestCase):
    cluster = None

    @classmethod
    def setupClass(cls):
        if not cls.cluster:
            cls.cluster = MiniCluster(None, nnport=50030)
        cls.cluster.mkdir("/tmp")

    @classmethod
    def tearDownClass(cls):
        if cls.cluster:
            cls.cluster.terminate()

    def setUp(self):
        self.fs = hdfs.client
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testconfig")
        hadoop_bin = os.path.join(os.environ['HADOOP_HOME'], 'bin/hadoop')
        hdfs.load_hadoop_cmd = lambda: [hadoop_bin, '--config', cfg_path]

    def tearDown(self):
        if self.fs.exists(self._test_dir()):
            self.fs.remove(self._test_dir(), skip_trash=True)

    @staticmethod
    def _test_dir():
        return '/tmp/luigi_tmp_testdir_%s' % getpass.getuser()

    @staticmethod
    def _test_file(suffix=""):
        return '%s/luigi_tmp_testfile%s' % (HdfsTestCase._test_dir(), suffix)


@attr('minicluster')
class ErrorHandling(HdfsTestCase):
    def test_connection_refused(self):
        """ The point of this test is to see if file existence checks
        can distinguish file non-existence from errors

        this test would fail if hdfs would run locally on port 0
        """
        self.assertRaises(
            hdfs.HDFSCliError,
            self.fs.exists,
            'hdfs://127.0.0.1:0/foo'
        )

    def test_mkdir_exists(self):
        path = self._test_dir()
        if not self.fs.exists(path):
            self.fs.mkdir(path)
        self.assertTrue(self.fs.exists(path))
        self.assertRaises(
            luigi.target.FileAlreadyExists,
            functools.partial(self.fs.mkdir, parents=False, raise_if_exists=True),
            path
        )
        self.fs.remove(path, skip_trash=True)


@attr('minicluster')
class AtomicHdfsOutputPipeTests(HdfsTestCase):

    def test_atomicity(self):
        testpath = self._test_dir()
        if self.fs.exists(testpath):
            self.fs.remove(testpath, skip_trash=True)

        pipe = hdfs.HdfsAtomicWritePipe(testpath)
        self.assertFalse(self.fs.exists(testpath))
        pipe.close()
        self.assertTrue(self.fs.exists(testpath))

    def test_with_close(self):
        testpath = self._test_file()
        try:
            if self.fs.exists(testpath):
                self.fs.remove(testpath, skip_trash=True)
        except:
            if self.fs.exists(self._test_dir()):
                self.fs.remove(self._test_dir(), skip_trash=True)

        with hdfs.HdfsAtomicWritePipe(testpath) as fobj:
            fobj.write('hej')

        self.assertTrue(self.fs.exists(testpath))

    def test_with_noclose(self):
        testpath = self._test_file()
        try:
            if self.fs.exists(testpath):
                self.fs.remove(testpath, skip_trash=True)
        except:
            if self.fs.exists(self._test_dir()):
                self.fs.remove(self._test_dir(), skip_trash=True)

        def foo():
            with hdfs.HdfsAtomicWritePipe(testpath) as fobj:
                fobj.write('hej')
                raise TestException('Test triggered exception')
        self.assertRaises(TestException, foo)
        self.assertFalse(self.fs.exists(testpath))


@attr('minicluster')
class HdfsAtomicWriteDirPipeTests(HdfsTestCase):
    def setUp(self):
        super(HdfsAtomicWriteDirPipeTests, self).setUp()
        self.path = self._test_file()
        if self.fs.exists(self.path):
            self.fs.remove(self.path, skip_trash=True)

    def test_atomicity(self):
        pipe = hdfs.HdfsAtomicWriteDirPipe(self.path)
        self.assertFalse(self.fs.exists(self.path))
        pipe.close()
        self.assertTrue(self.fs.exists(self.path))

    def test_readback(self):
        pipe = hdfs.HdfsAtomicWriteDirPipe(self.path)
        self.assertFalse(self.fs.exists(self.path))
        pipe.write("foo\nbar")
        pipe.close()
        self.assertTrue(hdfs.exists(self.path))
        dirlist = hdfs.listdir(self.path)
        datapath = '%s/data' % self.path
        returnlist = [d for d in dirlist]
        self.assertTrue(returnlist[0].endswith(datapath))
        pipe = hdfs.HdfsReadPipe(datapath)
        self.assertEqual(pipe.read(), "foo\nbar")

    def test_with_close(self):
        with hdfs.HdfsAtomicWritePipe(self.path) as fobj:
            fobj.write('hej')

        self.assertTrue(self.fs.exists(self.path))

    def test_with_noclose(self):
        def foo():
            with hdfs.HdfsAtomicWritePipe(self.path) as fobj:
                fobj.write('hej')
                raise TestException('Test triggered exception')
        self.assertRaises(TestException, foo)
        self.assertFalse(self.fs.exists(self.path))


# This class is a mixin, and does not inherit from TestCase, in order to avoid running the base class as a test case.
@attr('minicluster')
class _HdfsFormatTest(HdfsTestCase):
    format = None  # override with luigi.format.Format subclass

    def setUp(self):
        super(_HdfsFormatTest, self).setUp()
        self.target = hdfs.HdfsTarget(self._test_file(), format=self.format)
        if self.target.exists():
            self.target.remove(skip_trash=True)

    def test_with_write_success(self):
        with self.target.open('w') as fobj:
            fobj.write('foo')
        self.assertTrue(self.target.exists())

    def test_with_write_failure(self):
        def dummy():
            with self.target.open('w') as fobj:
                fobj.write('foo')
                raise TestException()

        self.assertRaises(TestException, dummy)
        self.assertFalse(self.target.exists())


@attr('minicluster')
class PlainFormatTest(_HdfsFormatTest, HdfsTestCase):
    format = hdfs.Plain


@attr('minicluster')
class PlainDirFormatTest(_HdfsFormatTest, HdfsTestCase):
    format = hdfs.PlainDir

    def test_multifile(self):
        with self.target.open('w') as fobj:
            fobj.write('foo\n')
        second = hdfs.HdfsTarget(self.target.path + '/data2', format=hdfs.Plain)

        with second.open('w') as fobj:
            fobj.write('bar\n')
        invisible = hdfs.HdfsTarget(self.target.path + '/_SUCCESS', format=hdfs.Plain)
        with invisible.open('w') as fobj:
            fobj.write('b0rk\n')
        self.assertTrue(second.exists())
        self.assertTrue(invisible.exists())
        self.assertTrue(self.target.exists())
        with self.target.open('r') as fobj:
            parts = fobj.read().strip('\n').split('\n')
            parts.sort()
        self.assertEqual(tuple(parts), ('bar', 'foo'))


@attr('minicluster')
class HdfsTargetTests(HdfsTestCase):

    def test_slow_exists(self):
        target = hdfs.HdfsTarget(self._test_file())
        try:
            target.remove(skip_trash=True)
        except:
            pass

        self.assertFalse(self.fs.exists(target.path))
        target.open("w").close()
        self.assertTrue(self.fs.exists(target.path))

        def should_raise():
            self.fs.exists("hdfs://doesnotexist/foo")
        self.assertRaises(hdfs.HDFSCliError, should_raise)

        def should_raise_2():
            self.fs.exists("hdfs://_doesnotexist_/foo")
        self.assertRaises(hdfs.HDFSCliError, should_raise_2)

    def test_atomicity(self):
        target = hdfs.HdfsTarget(self._test_file())
        if target.exists():
            target.remove(skip_trash=True)

        fobj = target.open("w")
        self.assertFalse(target.exists())
        fobj.close()
        self.assertTrue(target.exists())

    def test_readback(self):
        target = hdfs.HdfsTarget(self._test_file())
        if target.exists():
            target.remove(skip_trash=True)

        origdata = 'lol\n'
        fobj = target.open("w")
        fobj.write(origdata)
        fobj.close()

        fobj = target.open('r')
        data = fobj.read()
        self.assertEqual(origdata, data)

    def test_with_close(self):
        target = hdfs.HdfsTarget(self._test_file())
        if target.exists():
            target.remove(skip_trash=True)

        with target.open('w') as fobj:
            fobj.write('hej\n')

        self.assertTrue(target.exists())

    def test_with_exception(self):
        target = hdfs.HdfsTarget(self._test_file())
        if target.exists():
            target.remove(skip_trash=True)

        def foo():
            with target.open('w') as fobj:
                fobj.write('hej\n')
                raise TestException('Test triggered exception')
        self.assertRaises(TestException, foo)
        self.assertFalse(target.exists())

    def test_create_ancestors(self):
        parent = self._test_dir()
        target = hdfs.HdfsTarget("%s/foo/bar/baz" % parent)
        if self.fs.exists(parent):
            self.fs.remove(parent, skip_trash=True)
        self.assertFalse(self.fs.exists(parent))
        fobj = target.open('w')
        fobj.write('lol\n')
        fobj.close()
        self.assertTrue(self.fs.exists(parent))
        self.assertTrue(target.exists())

    def test_tmp_cleanup(self):
        path = self._test_file()
        target = hdfs.HdfsTarget(path, is_tmp=True)
        if target.exists():
            target.remove(skip_trash=True)
        with target.open('w') as fobj:
            fobj.write('lol\n')
        self.assertTrue(target.exists())
        del target
        import gc
        gc.collect()
        self.assertFalse(self.fs.exists(path))

    def test_luigi_tmp(self):
        target = hdfs.HdfsTarget(is_tmp=True)
        self.assertFalse(target.exists())
        with target.open('w'):
            pass
        self.assertTrue(target.exists())

    def test_tmp_move(self):
        target = hdfs.HdfsTarget(is_tmp=True)
        target2 = hdfs.HdfsTarget(self._test_file())
        if target2.exists():
            target2.remove(skip_trash=True)
        with target.open('w'):
            pass
        self.assertTrue(target.exists())
        target.move(target2.path)
        self.assertFalse(target.exists())
        self.assertTrue(target2.exists())

    def test_rename_no_parent(self):
        parent = self._test_dir() + '/foo'
        if self.fs.exists(parent):
            self.fs.remove(parent, skip_trash=True)

        target1 = hdfs.HdfsTarget(is_tmp=True)
        target2 = hdfs.HdfsTarget(parent + '/bar')
        with target1.open('w'):
            pass
        self.assertTrue(target1.exists())
        target1.move(target2.path)
        self.assertFalse(target1.exists())
        self.assertTrue(target2.exists())

    def test_rename_no_grandparent(self):
        grandparent = self._test_dir() + '/foo'
        if self.fs.exists(grandparent):
            self.fs.remove(grandparent, skip_trash=True)

        target1 = hdfs.HdfsTarget(is_tmp=True)
        target2 = hdfs.HdfsTarget(grandparent + '/bar/baz')
        with target1.open('w'):
            pass
        self.assertTrue(target1.exists())
        target1.move(target2.path)
        self.assertFalse(target1.exists())
        self.assertTrue(target2.exists())

    def test_glob_exists(self):
        target_dir = hdfs.HdfsTarget(self._test_dir())
        if target_dir.exists():
            target_dir.remove(skip_trash=True)
        self.fs.mkdir(target_dir.path)
        t1 = hdfs.HdfsTarget(target_dir.path + "/part-00001")
        t2 = hdfs.HdfsTarget(target_dir.path + "/part-00002")
        t3 = hdfs.HdfsTarget(target_dir.path + "/another")

        with t1.open('w') as f:
            f.write('foo\n')
        with t2.open('w') as f:
            f.write('bar\n')
        with t3.open('w') as f:
            f.write('biz\n')

        files = hdfs.HdfsTarget("%s/part-0000*" % target_dir.path)

        self.assertTrue(files.glob_exists(2))
        self.assertFalse(files.glob_exists(3))
        self.assertFalse(files.glob_exists(1))

    def assertRegexpMatches(self, text, expected_regexp, msg=None):
        """Python 2.7 backport."""
        if isinstance(expected_regexp, basestring):
            expected_regexp = re.compile(expected_regexp)
        if not expected_regexp.search(text):
            msg = msg or "Regexp didn't match"
            msg = '%s: %r not found in %r' % (msg, expected_regexp.pattern, text)
            raise self.failureException(msg)

    def test_tmppath_not_configured(self):
        #Given: several target paths to test
        path1 = "/dir1/dir2/file"
        path2 = "hdfs:///dir1/dir2/file"
        path3 = "hdfs://somehost/dir1/dir2/file"
        path4 = "file:///dir1/dir2/file"
        path5 = "/tmp/dir/file"
        path6 = "file:///tmp/dir/file"
        path7 = "hdfs://somehost/tmp/dir/file"
        path8 = None
        path9 = "/tmpdir/file"

        #When: I create a temporary path for targets
        res1 = hdfs.tmppath(path1, include_unix_username=False)
        res2 = hdfs.tmppath(path2, include_unix_username=False)
        res3 = hdfs.tmppath(path3, include_unix_username=False)
        res4 = hdfs.tmppath(path4, include_unix_username=False)
        res5 = hdfs.tmppath(path5, include_unix_username=False)
        res6 = hdfs.tmppath(path6, include_unix_username=False)
        res7 = hdfs.tmppath(path7, include_unix_username=False)
        res8 = hdfs.tmppath(path8, include_unix_username=False)
        res9 = hdfs.tmppath(path9, include_unix_username=False)

        #Then: I should get correct results relative to Luigi temporary directory
        self.assertRegexpMatches(res1,"^/tmp/dir1/dir2/file-luigitemp-\d+")
        #it would be better to see hdfs:///path instead of hdfs:/path, but single slash also works well
        self.assertRegexpMatches(res2, "^hdfs:/tmp/dir1/dir2/file-luigitemp-\d+")
        self.assertRegexpMatches(res3, "^hdfs://somehost/tmp/dir1/dir2/file-luigitemp-\d+")
        self.assertRegexpMatches(res4, "^file:///tmp/dir1/dir2/file-luigitemp-\d+")
        self.assertRegexpMatches(res5, "^/tmp/dir/file-luigitemp-\d+")
        #known issue with duplicated "tmp" if schema is present
        self.assertRegexpMatches(res6, "^file:///tmp/tmp/dir/file-luigitemp-\d+")
        #known issue with duplicated "tmp" if schema is present
        self.assertRegexpMatches(res7, "^hdfs://somehost/tmp/tmp/dir/file-luigitemp-\d+")
        self.assertRegexpMatches(res8, "^/tmp/luigitemp-\d+")
        self.assertRegexpMatches(res9,  "/tmp/tmpdir/file")

    def test_tmppath_username(self):
        self.assertRegexpMatches(hdfs.tmppath('/path/to/stuff', include_unix_username=True),
                                 "^/tmp/[a-z0-9_]+/path/to/stuff-luigitemp-\d+")


TIMESTAMP_DELAY = 60 # Big enough for `hadoop fs`?
@attr('minicluster')
class _HdfsClientTest(HdfsTestCase):

    def create_file(self, target):
        fobj = target.open("w")
        fobj.close()

    def put_file(self, local_target, local_filename, target_path, delpath=True):
        if local_target.exists():
            local_target.remove()
        self.create_file(local_target)

        if delpath:
            target = hdfs.HdfsTarget(target_path)
            if target.exists():
                target.remove(skip_trash=True)
            self.fs.mkdir(target.path)

        self.fs.put(local_target.path, target_path)
        target_file_path = target_path + "/" + local_filename
        return hdfs.HdfsTarget(target_file_path)

    def test_put(self):
        local_dir = "test/data"
        local_filename = "file1.dat"
        local_path = "%s/%s" % (local_dir, local_filename)
        target_path = self._test_dir()

        local_target = luigi.LocalTarget(local_path)
        target = self.put_file(local_target, local_filename, target_path)
        self.assertTrue(target.exists())
        local_target.remove()

    def test_get(self):
        local_dir = "test/data"
        local_filename = "file1.dat"
        local_path = "%s/%s" % (local_dir, local_filename)
        target_path = self._test_dir()

        local_target = luigi.LocalTarget(local_path)
        target = self.put_file(local_target, local_filename, target_path)
        self.assertTrue(target.exists())
        local_target.remove()

        local_copy_path = "%s/file1.dat.cp" % local_dir
        local_copy = luigi.LocalTarget(local_copy_path)
        if local_copy.exists():
            local_copy.remove()
        self.fs.get(target.path, local_copy_path)
        self.assertTrue(local_copy.exists())
        local_copy.remove()

    def test_getmerge(self):
        local_dir = "test/data"
        local_filename1 = "file1.dat"
        local_path1 = "%s/%s" % (local_dir, local_filename1)
        local_filename2 = "file2.dat"
        local_path2 = "%s/%s" % (local_dir, local_filename2)
        target_dir = self._test_dir()

        local_target1 = luigi.LocalTarget(local_path1)
        target1 = self.put_file(local_target1, local_filename1, target_dir)
        self.assertTrue(target1.exists())
        local_target1.remove()

        local_target2 = luigi.LocalTarget(local_path2)
        target2 = self.put_file(local_target2, local_filename2, target_dir)
        self.assertTrue(target2.exists())
        local_target2.remove()

        local_copy_path = "%s/file.dat.cp" % (local_dir)
        local_copy = luigi.LocalTarget(local_copy_path)
        if local_copy.exists():
            local_copy.remove()
        self.fs.getmerge(target_dir, local_copy_path)
        self.assertTrue(local_copy.exists())
        local_copy.remove()

        local_copy_crc_path = "%s/.file.dat.cp.crc" % (local_dir)
        local_copy_crc = luigi.LocalTarget(local_copy_crc_path)
        self.assertTrue(local_copy_crc.exists())
        local_copy_crc.remove()

    def _setup_listdir(self):
        """Create the test directory, and things in it."""
        target_dir = self._test_dir()
        local_dir = "test/data"

        local_filename1 = "file1.dat"
        local_path1 = "%s/%s" % (local_dir, local_filename1)
        local_target1 = luigi.LocalTarget(local_path1)
        target1 = self.put_file(local_target1, local_filename1, target_dir)
        self.assertTrue(target1.exists())

        local_filename2 = "file2.dat"
        local_path2 = "%s/%s" % (local_dir, local_filename2)
        local_target2 = luigi.LocalTarget(local_path2)
        target2 = self.put_file(local_target2, local_filename2,
                                target_dir, delpath=False)
        self.assertTrue(target2.exists())

        local_filename3 = "file3.dat"
        local_path3 = "%s/%s" % (local_dir, local_filename3)
        local_target3 = luigi.LocalTarget(local_path3)
        target3 = self.put_file(local_target3, local_filename3,
                                target_dir + '/sub1')
        self.assertTrue(target3.exists())

        local_filename4 = "file4.dat"
        local_path4 = "%s/%s" % (local_dir, local_filename4)
        local_target4 = luigi.LocalTarget(local_path4)
        target4 = self.put_file(local_target4, local_filename4,
                                target_dir + '/sub2')
        self.assertTrue(target4.exists())

        return target_dir

    def test_listdir_base_list(self):
        """Verify we get the base four items created by _setup_listdir()"""
        path = self._setup_listdir()
        dirlist = self.fs.listdir(path, ignore_directories=False,
                                  ignore_files=False, include_size=False,
                                  include_type=False, include_time=False,
                                  recursive=False)
        entries = [dd for dd in dirlist]
        self.assertEquals(4, len(entries), msg="%r" % entries)
        self.assertEquals(path + '/file1.dat', entries[0], msg="%r" % entries)
        self.assertEquals(path + '/file2.dat', entries[1], msg="%r" % entries)
        self.assertEquals(path + '/sub1', entries[2], msg="%r" % entries)
        self.assertEquals(path + '/sub2', entries[3], msg="%r" % entries)

    def test_listdir_base_list_files_only(self):
        """Verify we get the base two files created by _setup_listdir()"""
        path = self._setup_listdir()
        dirlist = self.fs.listdir(path, ignore_directories=True,
                                  ignore_files=False, include_size=False,
                                  include_type=False, include_time=False,
                                  recursive=False)
        entries = [dd for dd in dirlist]
        self.assertEquals(2, len(entries), msg="%r" % entries)
        self.assertEquals(path + '/file1.dat', entries[0], msg="%r" % entries)
        self.assertEquals(path + '/file2.dat', entries[1], msg="%r" % entries)

    def test_listdir_base_list_dirs_only(self):
        """Verify we get the base two directories created by _setup_listdir()"""
        path = self._setup_listdir()
        dirlist = self.fs.listdir(path, ignore_directories=False,
                                  ignore_files=True, include_size=False,
                                  include_type=False, include_time=False,
                                  recursive=False)
        entries = [dd for dd in dirlist]
        self.assertEquals(2, len(entries), msg="%r" % entries)
        self.assertEquals(path + '/sub1', entries[0], msg="%r" % entries)
        self.assertEquals(path + '/sub2', entries[1], msg="%r" % entries)

    def test_listdir_base_list_recusion(self):
        """Verify we get the every item created by _setup_listdir()"""
        path = self._setup_listdir()
        dirlist = self.fs.listdir(path, ignore_directories=False,
                                  ignore_files=False, include_size=False,
                                  include_type=False, include_time=False,
                                  recursive=True)
        entries = [dd for dd in dirlist]
        self.assertEquals(6, len(entries), msg="%r" % entries)
        self.assertEquals(path + '/file1.dat', entries[0], msg="%r" % entries)
        self.assertEquals(path + '/file2.dat', entries[1], msg="%r" % entries)
        self.assertEquals(path + '/sub1', entries[2], msg="%r" % entries)
        self.assertEquals(path + '/sub1/file3.dat', entries[3], msg="%r" % entries)
        self.assertEquals(path + '/sub2', entries[4], msg="%r" % entries)
        self.assertEquals(path + '/sub2/file4.dat', entries[5], msg="%r" % entries)

    def test_listdir_base_list_get_sizes(self):
        """Verify we get sizes for the two base files."""
        path = self._setup_listdir()
        dirlist = self.fs.listdir(path, ignore_directories=False,
                                  ignore_files=False, include_size=True,
                                  include_type=False, include_time=False,
                                  recursive=False)
        entries = [dd for dd in dirlist]
        self.assertEquals(4, len(entries), msg="%r" % entries)
        self.assertEquals(2, len(entries[0]), msg="%r" % entries)
        self.assertEquals(path + '/file1.dat', entries[0][0], msg="%r" % entries)
        self.assertEquals(0, entries[0][1], msg="%r" % entries)
        self.assertEquals(2, len(entries[1]), msg="%r" % entries)
        self.assertEquals(path + '/file2.dat', entries[1][0], msg="%r" % entries)
        self.assertEquals(0, entries[1][1], msg="%r" % entries)

    def test_listdir_base_list_get_types(self):
        """Verify we get the types for the four base items."""
        path = self._setup_listdir()
        dirlist = self.fs.listdir(path, ignore_directories=False,
                                  ignore_files=False, include_size=False,
                                  include_type=True, include_time=False,
                                  recursive=False)
        entries = [dd for dd in dirlist]
        self.assertEquals(4, len(entries), msg="%r" % entries)
        self.assertEquals(2, len(entries[0]), msg="%r" % entries)
        self.assertEquals(path + '/file1.dat', entries[0][0], msg="%r" % entries)
        self.assertTrue(re.match(r'[-f]', entries[0][1]), msg="%r" % entries)
        self.assertEquals(2, len(entries[1]), msg="%r" % entries)
        self.assertEquals(path + '/file2.dat', entries[1][0], msg="%r" % entries)
        self.assertTrue(re.match(r'[-f]', entries[1][1]), msg="%r" % entries)
        self.assertEquals(2, len(entries[2]), msg="%r" % entries)
        self.assertEquals(path + '/sub1', entries[2][0], msg="%r" % entries)
        self.assertEquals('d', entries[2][1], msg="%r" % entries)
        self.assertEquals(2, len(entries[3]), msg="%r" % entries)
        self.assertEquals(path + '/sub2', entries[3][0], msg="%r" % entries)
        self.assertEquals('d', entries[3][1], msg="%r" % entries)

    def test_listdir_base_list_get_times(self):
        """Verify we get the times, even if we can't fully check them."""
        path = self._setup_listdir()
        dirlist = self.fs.listdir(path, ignore_directories=False,
                                  ignore_files=False, include_size=False,
                                  include_type=False, include_time=True,
                                  recursive=False)
        entries = [dd for dd in dirlist]
        self.assertEquals(4, len(entries), msg="%r" % entries)
        self.assertEquals(2, len(entries[0]), msg="%r" % entries)
        self.assertEquals(path + '/file1.dat', entries[0][0], msg="%r" % entries)
        self.assertTrue(timegm(datetime.now().timetuple()) -
                        timegm(entries[0][1].timetuple()) < TIMESTAMP_DELAY) 

    def test_listdir_full_list_get_everything(self):
        """Verify we get all the values, even if we can't fully check them."""
        path = self._setup_listdir()
        dirlist = self.fs.listdir(path, ignore_directories=False,
                                  ignore_files=False, include_size=True,
                                  include_type=True, include_time=True,
                                  recursive=True)
        entries = [dd for dd in dirlist]
        self.assertEquals(6, len(entries), msg="%r" % entries)
        self.assertEquals(4, len(entries[0]), msg="%r" % entries)
        self.assertEquals(path + '/file1.dat', entries[0][0], msg="%r" % entries)
        self.assertEquals(0, entries[0][1], msg="%r" % entries)
        self.assertTrue(re.match(r'[-f]', entries[0][2]), msg="%r" % entries)
        self.assertTrue(timegm(datetime.now().timetuple()) -
                        timegm(entries[0][3].timetuple()) < TIMESTAMP_DELAY)
        self.assertEquals(4, len(entries[1]), msg="%r" % entries)
        self.assertEquals(path + '/file2.dat', entries[1][0], msg="%r" % entries)
        self.assertEquals(4, len(entries[2]), msg="%r" % entries)
        self.assertEquals(path + '/sub1', entries[2][0], msg="%r" % entries)
        self.assertEquals(4, len(entries[3]), msg="%r" % entries)
        self.assertEquals(path + '/sub1/file3.dat', entries[3][0], msg="%r" % entries)
        self.assertEquals(4, len(entries[4]), msg="%r" % entries)
        self.assertEquals(path + '/sub2', entries[4][0], msg="%r" % entries)
        self.assertEquals(4, len(entries[5]), msg="%r" % entries)
        self.assertEquals(path + '/sub2/file4.dat', entries[5][0], msg="%r" % entries)

    @mock.patch('luigi.hdfs.call_check')
    def test_cdh3_client(self, call_check):
        cdh3_client = luigi.hdfs.HdfsClientCdh3()
        cdh3_client.remove("/some/path/here")
        call_check.assert_called_once_with(['hadoop', 'fs', '-rmr', '/some/path/here'])

        cdh3_client.remove("/some/path/here", recursive=False)
        self.assertEquals(mock.call(['hadoop', 'fs', '-rm', '/some/path/here']), call_check.call_args_list[-1])

    @mock.patch('subprocess.Popen')
    def test_apache1_client(self, popen):
        comm = mock.Mock(name='communicate_mock')
        comm.return_value = "some return stuff", ""

        preturn = mock.Mock(name='open_mock')
        preturn.returncode = 0
        preturn.communicate = comm
        popen.return_value = preturn

        apache_client = luigi.hdfs.HdfsClientApache1()
        returned = apache_client.exists("/some/path/somewhere")
        self.assertTrue(returned)

        preturn.returncode = 1
        returned = apache_client.exists("/some/path/somewhere")
        self.assertFalse(returned)

        preturn.returncode = 13
        self.assertRaises(luigi.hdfs.HDFSCliError, apache_client.exists, "/some/path/somewhere")

if __name__ == "__main__":
    unittest2.main()
    # Uncomment to run a single test
    # unittest.TextTestRunner(failfast=True, verbosity=2).run(suite())

# def suite():
#     suite = unittest.TestSuite()
#     suite.addTest(unittest.makeSuite(HdfsTargetTests, prefix='test_tmppath'))
#     return suite

