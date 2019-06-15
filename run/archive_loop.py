import argparse
import datetime
import distutils
import glob
import logging
import os
import pathlib
import shutil
import subprocess
import time

import boto3

from typing import List
from logging.handlers import RotatingFileHandler

CAM_MOUNT='/mnt/cam'
CAM_DIR='TeslaCam'
MUSIC_MOUNT='/mnt/music'
ARCHIVE_MOUNT='/mnt/archive'
LOG_FILE='/mutable/archiveloop.log'
SNS_FILE='/root/.teslaCamSNSTopicARN'
GB = 1024 * 1024 * 1024

EXCLUDED_FILES= [ ]

class Fileinfo:
    def __init__(self, path: pathlib.Path):
        self._path = path
        stat = path.stat()
        self._size = stat.st_size
        self._date = stat.st_mtime

class Filesinfo:
    def __init__(self):
        self._files = []
        self._size = 0

    def append(self, fileinfo: Fileinfo):
        self._size += fileinfo._size
        self._files.append(fileinfo)

class TeslaCamArchiver:
    def __init__(self, archive_host: str,  archive_path: str, cam_path: str, music_path: str,
                 log_file: str, max_size: int, dryrun: bool = False, debug: bool = False,
                 sleep_time: int = 3600):
        self._log_file = log_file
        self._archive_host = archive_host
        self._max_size = max_size * GB
        self._sleep_time = sleep_time
        self._archive_path = pathlib.Path(archive_path)
        self._cam_path = pathlib.Path(cam_path)
        self._music_path = pathlib.Path(music_path)
        self._dryrun = dryrun
        self._debug = debug
        self._logger = self._init_logging(debug)

        if os.path.exists(SNS_FILE):
            import configparser
            config = configparser.ConfigParser()
            config.read(SNS_FILE)
            self._SNSTopic = config['SNS']['sns_topic_arn']
        else:
            self._SNSTopic = None

    def _init_logging(self, debug: bool):
        level = logging.DEBUG if debug else logging.INFO

        logger = logging.getLogger('TeslaCam')
        if self._debug:
            c_handler = logging.StreamHandler()
            c_handler.setFormatter(logging.Formatter('%(asctime)15s - %(message)s'))
            c_handler.setLevel(level)
            logger.addHandler(c_handler)

        f_handler = RotatingFileHandler(filename=self._log_file, maxBytes=1024*1024, backupCount=5)
        f_handler.setFormatter(logging.Formatter('%(asctime)15s - %(message)s'))
        f_handler.setLevel(level)
        logger.addHandler(f_handler)
        logger.setLevel(level)

        return logger

    def _execute(self, args: List[str], silent: bool = False) -> bool:
        try:
            lines = subprocess.check_output(args)
            self._logger.debug(lines)
            return True
        except Exception as e:
            if not silent:
                self._logger.error('Exception {}'.format(str(e)))
            return False

    def _fix_errors_in_mount_point(self, mount_point: pathlib.Path):
        self._logger.info('Running fsck on {}'.format(mount_point))
        self._execute(['/sbin/fsck', str(mount_point), '--', '-a'])
        self._logger.info('Finished fsck on {}'.format(mount_point))

    def _archive_is_reachable(self):
        response = os.system("ping -c 1 " + self._archive_host + " >/dev/null 2>&1")
        return response == 0

    def _connect_usb_drives_to_host(self):
        self._logger.info("Connecting usb to host...")
        self._execute(['modprobe', 'g_mass_storage'])
        self._logger.info("Connected usb to host.")

    def _disconnect_usb_drives_from_host(self):
        self._logger.info("Disconnecting usb to host...")
        self._execute(['modprobe', '-r', 'g_mass_storage'])
        self._logger.info("Disconnected usb to host.")

    def _wait_for_archive_to_be_reachable(self):
        self._logger.info("Waiting for archive to be reachable...")
        while not self._archive_is_reachable():
            time.sleep(5)

        self._logger.info('Archive is reachable.')

    def _is_mounted(self, mount_point: pathlib.Path):
        return self._execute(['findmnt', '--mountpoint', str(mount_point)], silent = True)

    def _mount_mountpoint(self, mount_point: pathlib.Path):
        if self._is_mounted(mount_point):
            self._logger.info("{} already mounted.".format(mount_point))
            return True
        else:
            if self._execute(['mount', str(mount_point)]):
                self._logger.info('Mounted {}'.format(mount_point))
                return True
            else:
                self._logger.info('Failed to mount {}'.format(mount_point))
                return False

    def _unmount_mount_point(self, mount_point: pathlib.Path):
        if not self._is_mounted(mount_point):
            self._logger.info("{} already unmounted.".format(mount_point))
            return True
        else:
            if self._execute(['umount', str(mount_point)]):
                self._logger.info('Unmounted {}'.format(mount_point))
                return True
            else:
                self._logger.info('Failed to unmount {}'.format(mount_point))
                return False

    def _ensure_mount_point_is_mounted(self, mount_point: str):
        while not self._mount_mountpoint(mount_point):
            time.sleep(5)
          
    def _mount_and_fix_errors(self, mount_point: pathlib.Path):
        self._logger.info('Mount {} and running fsck...'.format(mount_point))
        if mount_point.exists():
            self._ensure_mount_point_is_mounted(mount_point)

            self._fix_errors_in_mount_point(mount_point)
            while not self._unmount_mount_point(mount_point):
                time.sleep(5)
        self._logger.info('Done Mount {} and running fsck...'.format(mount_point))

    def _get_all_files_information(self, dir: pathlib.Path) -> Filesinfo:
        filesinfo = Filesinfo()

        for root, dirnames, filenames in os.walk(str(dir)):
            for filename in filenames:
                if filename not in EXCLUDED_FILES:
                    filesinfo.append(Fileinfo(pathlib.Path(root).joinpath(filename)))
                    self._logger.debug('Found file {}'.format(pathlib.Path(root).joinpath(filename)))

        filesinfo._files.sort(key=lambda x: x._date)
        return filesinfo
        
    def _delete_files(self, files: Filesinfo, size_to_delete: int):
        self._logger.info('Deleting files...')
        deleted = 0
        deleted_size = 0
        for file in files._files:
            deleted += 1
            deleted_size += file._size

            self._logger.debug('Deleting {}'.format(file._path))
            if not self._dryrun:
                os.unlink(os._path)

            if deleted_size >= size_to_delete:
                break

        self._logger.info('Deleted {} files for a total of {} GB'.format(deleted, deleted_size / GB))
        return (deleted, deleted_size)

    def _delete_empty_folders(self, directory: pathlib.Path, remove_root: bool):
        if not os.path.isdir(directory):
            return

        files = os.listdir(directory)
        if len(files):
            for f in files:
                fullpath = os.path.join(directory, f)
                if os.path.isdir(fullpath):
                    self._delete_empty_folders(fullpath, True)

        files = os.listdir(directory)
        if len(files) == 0 and remove_root:
            os.rmdir(directory)

    def _move_files(self, files: Filesinfo, base_path: pathlib.Path):
        self._logger.info('Moving files...')
        moved = 0
        moved_size = 0
        created = []
        for file in files._files:
            moved += 1
            moved_size += file._size

            dst_path = self._archive_path.joinpath(file._path.relative_to(base_path))
            self._logger.debug('Moving {} to {}'.format(file._path, dst_path))
            if not self._dryrun:
                if dst_path.parent not in created:
                    created.append(dst_path.parent)
                    if not dst_path.parent.exists():
                        os.makedirs(str(dst_path.parent))

                shutil.move(str(file._path), str(dst_path))

        self._delete_empty_folders(self._cam_path.joinpath(CAM_DIR), False)

        self._logger.info('Moved {} files for a total of {} GB'.format(moved, moved_size / GB))
        return (moved, moved_size)

    def _send_sns(self, message: str):
        if self._SNSTopic:
            sns = boto3.client('sns')
            response = sns.publish(TopicArn=self._SNSTopic, Message=message, Subject="TeslaCam Message!")
            self._logger.debug(response)
  
    def _do_archiving(self):
        self._logger.info('Starting to archive...')
        archived_files = self._get_all_files_information(self._archive_path)
        cam_files = self._get_all_files_information(self._cam_path.joinpath(CAM_DIR))
        total_size = archived_files._size + cam_files._size

        self._logger.debug('Total_size {}, max_size {}'.format(total_size, self._max_size))

        deleted = 0
        deleted_size = 0
        if total_size > self._max_size:
            deleted, deleted_size = self._delete_files(archived_files, total_size - self._max_size)
        
        moved, moved_size = self._move_files(cam_files, self._cam_path.joinpath(CAM_DIR))

        self._send_sns("{} file(s) for {}GB were copied.".format(moved, moved_size / GB))
        self._logger.info('Done archiving.')

    def _archive_teslacam_clips(self):
        self._logger.info('Starting the archiving process...')
        try:
            self._disconnect_usb_drives_from_host()

            self._ensure_mount_point_is_mounted(self._cam_path)
            self._fix_errors_in_mount_point(self._cam_path)

            self._do_archiving()

            self._unmount_mount_point(self._cam_path)
            self._logger.info('Archiving process completed')
        except Exception as e:
            self._logger.error('Archiving process failed Exception {}'.format(str(e)))
            
        self._connect_usb_drives_to_host()

    def do_archive_loop(self):
        self._logger.info('Starting archiving loop')

        self._disconnect_usb_drives_from_host()

        self._mount_and_fix_errors(self._music_path)
        self._mount_and_fix_errors(self._cam_path)

        self._connect_usb_drives_to_host()

        last_copy = datetime.datetime.min
        while True:
            now = datetime.datetime.utcnow()
            if (now - last_copy).total_seconds() >= self._sleep_time and self._archive_is_reachable():
                self._archive_teslacam_clips()
                last_copy = datetime.datetime.utcnow()
            else:
                sleep_time = max((now - last_copy).total_seconds(), self._sleep_time)
                self._logger.info('Nothing to do, sleeping {} seconds'.format(sleep_time))
                time.sleep(sleep_time)
           
if __name__== "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-n', '--hostname', help='The hostname of the archive')
    parser.add_argument('-m', '--maxsize', default="100", help='Max size of the archives to keep, in GB')
    parser.add_argument('-a', '--archivepath', default=ARCHIVE_MOUNT, help='The path of the archive disk')
    parser.add_argument('-c', '--campath', default=CAM_MOUNT, help='The path of the camera mount')
    parser.add_argument('-t', '--musicpath', default=MUSIC_MOUNT, help='The path of the music mount')
    parser.add_argument('-l', '--logfile', default=LOG_FILE, help='The path of the log file')
    parser.add_argument('-s', '--sleep', default="3600", help='Number of seconds to sleep between moving files if wifi is available')
    parser.add_argument('-d', '--debug', action='store_true', help='Debug mode and display information on stdout also')
    parser.add_argument('--dryrun', action='store_true', help='Dry-run mode')

    args = parser.parse_args()

    if args.hostname is None:
        print('Must pass a hostname for the archive.')
        parser.print_usage()
    else:
        archiver = TeslaCamArchiver(args.hostname, args.archivepath, args.campath, args.musicpath,
                                    args.logfile, int(args.maxsize), dryrun = args.dryrun,
                                    debug = args.debug, sleep_time = int(args.sleep))
        archiver.do_archive_loop()
