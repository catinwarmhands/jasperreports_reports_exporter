import os
import io
import re
import ast
import sys
import time
import getpass
import zipfile
import loguru
import inspect
import datetime
import requests
import traceback
import itertools
import functools
import subprocess
import multiprocessing
import magic          # type: ignore[import]
import pyjson5        # type: ignore[import]
import colorama       # type: ignore[import]
import xmltodict      # type: ignore[import]
from tqdm import tqdm # type: ignore[import]
from abc import ABC
from enum import Enum
from dataclasses import dataclass
from collections import OrderedDict
from requests.auth import HTTPBasicAuth
from typing import Dict, List, Tuple, Optional, Final, Generator, Union, Callable, Any


__version__               : Final[str] = "3.0.1"
UTF_8                     : Final[str] = "utf-8"
ZIP                       : Final[str] = "zip"
XML                       : Final[str] = "xml"
JRXML                     : Final[str] = "jrxml"
LOG_DIR                   : Final[str] = "log"
CONFIG_FILENAME           : Final[str] = "config.json"
DEBUG_PREFIX              : Final[str] = "debug_"
BATCH_REMOTE_REPORT_LABEL : Final[str] = "batch_remote_output"
BATCH_LOCAL_REPORT_LABEL  : Final[str] = "batch_local_output"
BAR_FORMAT                : Final[str] = "{l_bar}{bar}| {remaining} left"
LOCK_FILENAME             : Final[str] = "lock.txt"


def running_via_pyinstall() -> bool:
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def real_path(p: str) -> str:
    root = sys.argv[0] if running_via_pyinstall() else __file__
    return os.path.abspath(os.path.join(os.path.dirname(root), p)).replace("\\","/")


def pause():
    if running_via_pyinstall():
        logger.info("Press Enter to continue...")
        input()


# Декоратор. Выводит в info время работы функции в секундах
def timing(f):
    @functools.wraps(f)
    def wrap(*args, **kw):
        ts = time.time()
        result = f(*args, **kw)
        te = time.time()
        logger.info(f"{f.__name__.capitalize()} done in {te-ts:.2f} seconds")
        return result
    return wrap


# Декоратор. Делает n попыток выполнить функцию, после чего прокидывает исключение
def retry(n=3):
    def decorate(f):
        @functools.wraps(f)
        def applicator(*args, **kwargs):
            i = 0
            while True:
                i += 1
                try:
                    return f(*args,**kwargs)
                except Exception as e:
                    logger.debug(f"Error in {f.__name__} (attempt {i}/{n}): {e}")
                    logger.debug(traceback.format_exc())
                    if i >= n:
                        raise
        return applicator
    return decorate


# Декоратор. Если из функции вылетело исключение, то логгирует его, и возвращает value
def return_on_failure(value):
    def decorate(f):
        @functools.wraps(f)
        def applicator(*args, **kwargs):
            try:
                return f(*args,**kwargs)
            except Exception as e:
                logger.debug(f"Error in {f.__name__}: {e}")
                logger.debug(traceback.format_exc())
            return value
        return applicator
    return decorate


class Phase(Enum):
    CREATED     = "created"
    FAILED      = "failed"
    FINISHED    = "finished"
    IN_PROGRESS = "inprogress"


@dataclass
class Report:
    job:         "Job" # Forward reference
    label:       Optional[str]
    data:        Optional[bytes]
    message:     Optional[str]
    phase:       Optional[Phase]
    file_format: Optional[str]
    output_dir           : str


@dataclass
class AbstractRemoteReport(ABC, Report):
    export_id: Optional[str]


@dataclass
class RemoteReport(AbstractRemoteReport):
    report_uri: Optional[str]


@dataclass
class BatchedRemoteReport(AbstractRemoteReport):
    report_uris: Optional[List[str]]


@dataclass
class LocalReport(Report):
    local_path: Optional[str]


class JasperReportsAPI:

    @functools.cache # type: ignore[misc]
    def __init__(self, host_url: str, username: str, password: str):
        if host_url[-1] != '/':
            host_url += '/'
        self._host_url   : str = host_url
        self._export_url : str = f"{self._host_url}rest_v2/export/"
        self._auth       : HTTPBasicAuth = HTTPBasicAuth(username, password)
        self._fs         : List[Dict[str, str]] = self.get_all_resources_list()

    @return_on_failure((None, Phase.FAILED))
    @retry(3)
    def request_export(self, report: AbstractRemoteReport) -> Tuple[Optional[str], Phase]:
        uris: Optional[List[str]]

        if isinstance(report, RemoteReport):
            uris = [report.report_uri]  # type: ignore[list-item]
        elif isinstance(report, BatchedRemoteReport):
            uris = report.report_uris

        params = {
            "uris": uris,
            "parameters": [
                "skip-dependent-resources",
                "skip-suborganizations",
                "skip-attribute-values",
            ]
        }
        res = requests.post(
            url=self._export_url,
            json=params,
            auth=self._auth,
            verify=False
        )
        res.raise_for_status()
        logger.debug(f"request_export: {res.text}")
        response: OrderedDict = xmltodict.parse(res.text)
        export_id = response["state"]["id"]
        phase = Phase(response["state"]["phase"])
        return export_id, phase

    @return_on_failure((None, Phase.FAILED))
    @retry(3)
    def check_export_status(self, report: AbstractRemoteReport) -> Tuple[Optional[str], Phase]:
        res = requests.get(
            url=f"{self._export_url}{report.export_id}/state",
            auth=self._auth,
            verify=False
        )
        res.raise_for_status()
        logger.debug(f"check_export_status: {res.text}")
        response: OrderedDict = xmltodict.parse(res.text)
        phase = Phase(response["state"]["phase"])
        message = response["state"]["message"]
        return message, phase

    @return_on_failure(None)
    def get_label(self, uri: str) -> Optional[str]:
        for e in self._fs:
            if uri == e["uri"]:
                return e["label"]
        return ""

    @return_on_failure(None)
    @retry(3)
    def fetch_result(self, report: AbstractRemoteReport) -> Optional[bytes]:
        if report.export_id is None:
            raise ValueError("export_id is None")
        res = requests.get(
            url=f"{self._export_url}{report.export_id}/{report.export_id}.zip",
            auth=self._auth,
            verify=False
        )
        res.raise_for_status()
        return res.content

    @return_on_failure(None)
    @retry(3)
    def get_resources_list(self, resource_type: str):
        params = {
            "type": resource_type,
            "limit":"1000000",
            "expanded":"true",
        }
        res = requests.get(
            url=f"{self._host_url}rest_v2/resources",
            auth=self._auth,
            params=params,
            verify=False
        )
        res.raise_for_status()

        response: OrderedDict = xmltodict.parse(res.text)
        return response["resources"]["resourceLookup"]

    @return_on_failure(None)
    def get_all_resources_list(self):
        folders = self.get_resources_list("folder")
        reports = self.get_resources_list("reportUnit")

        fs = []

        for d in folders:
            fs.append({
                  "resourceType": d["resourceType"],
                  "uri": d["uri"],
                  "label": d["label"],
            })

        for d in reports:
            fs.append({
                  "resourceType": d["resourceType"],
                  "uri": d["uri"],
                  "label": d["label"],
            })
        return fs

    def get_resource_type(self, uri):
        for e in self._fs:
            if uri == e["uri"]:
                return e["resourceType"]

    def _get_children(self, uri):
        result = []
        for e in self._fs:
            if re.match(fr"^{uri}/\w*$", e["uri"]):
                result.append(e)
        return result

    def fs_walker(self, uri):
        c = self._get_children(uri)
        for e in c:
            yield e
            yield from self.fs_walker(e["uri"])

    def get_fancy_path(self, uri, root=None):
        result = []
        while True:
            dir_uri = os.path.dirname(uri)
            dir_label = self.get_label(dir_uri)
            if not dir_label or dir_uri == root:
                break
            result.append(dir_label)
            uri = dir_uri

        return "/".join(reversed(result))


@dataclass
class Job:
    config_path          : str
    job_id               : int
    jrs_api              : JasperReportsAPI
    base_output_dir      : str
    batch_export         : bool
    watermark            : bool
    commit               : bool
    process_file_formats : List[str]
    file_rename          : Dict[str, str]
    text_replace         : Dict[str, str]
    output_rename        : Dict[str, str]
    reports_uris         : List[str]


def local_fs_walker(p: str):
    files = [f"{p}/{f}" for f in os.listdir(p)]
    for f in files:
        yield f
        if os.path.isdir(f):
            yield from local_fs_walker(f)


def build_jobs(configs_paths: List[str]) -> List[Job]:
    def get_value(d, key, default):
        return d[key] if key in d else default

    result: List[Job] = []
    for config_path in configs_paths:
        try:
            with open(real_path(config_path), mode="r", encoding=UTF_8) as f:
                config = pyjson5.loads(f.read())

            global_host_url             : str            = get_value(config, "host_url",             "http://localhost:80/jasperserver/")
            global_username             : str            = get_value(config, "username",             "jasperadmin")
            global_password             : str            = get_value(config, "password",             "jasperadmin")
            global_output_dir           : str            = get_value(config, "output_dir",           "output")
            global_batch_export         : bool           = get_value(config, "batch_export",         False)
            global_watermark            : bool           = get_value(config, "watermark",            False)
            global_commit               : bool           = get_value(config, "commit",               False)
            global_process_file_formats : List[str]      = get_value(config, "process_file_formats", [XML])
            global_file_rename          : Dict[str, str] = get_value(config, "file_rename",          {})
            global_text_replace         : Dict[str, str] = get_value(config, "text_replace",         {})
            global_output_rename        : Dict[str, str] = get_value(config, "output_rename",        {})
            global_reports_uris         : List[str]      = get_value(config, "reports",              [])

            jobs: List[Dict[str, Any]] = get_value(config, "jobs", [])

            if len(jobs) == 0:
                jobs.append({})
            elif len(global_reports_uris) != 0:
                jobs.append({"reports_uris": global_reports_uris})

            for i, job in enumerate(jobs):
                job_host_url : str = get_value(job, "host_url", global_host_url)
                job_username : str = get_value(job, "username", global_username)
                job_password : str = get_value(job, "password", global_password)
                jrs_api = JasperReportsAPI(job_host_url, job_username, job_password)

                job_process_file_formats = get_value(job, "process_file_formats", global_process_file_formats)
                for i, e in enumerate(job_process_file_formats):
                    e = e.lower()
                    if e and e[0] == '.':
                        job_process_file_formats[i] = e[1:]
                if ZIP in job_process_file_formats:
                    raise ValueError("Value \"zip\" is not allowed in field \"process_file_formats\"")

                result.append(Job(
                    config_path          = config_path,
                    job_id               = i,
                    jrs_api              = jrs_api,
                    base_output_dir      = get_value(job, "output_dir",           global_output_dir),
                    batch_export         = get_value(job, "batch_export",         global_batch_export),
                    watermark            = get_value(job, "watermark",            global_watermark),
                    commit               = get_value(job, "commit",               global_commit),
                    process_file_formats = job_process_file_formats,
                    file_rename          = get_value(job, "file_rename",          global_file_rename),
                    text_replace         = get_value(job, "text_replace",         global_text_replace),
                    output_rename        = get_value(job, "output_rename",        global_output_rename),
                    reports_uris         = get_value(job, "reports",              global_reports_uris),
                ))

        except KeyError as e:
            raise Exception(f"Config file is missing key: {e}!")
        except Exception as e:
            logger.debug(traceback.format_exc())
            raise Exception(f"Error loading config file \"{config_path}\"!\n{e}")
    return result


def build_reports(jobs: List[Job]) -> List[Report]:
    result: List[Report] = []
    for job in jobs:
        if len(job.reports_uris) == 0:
            raise ValueError(f"Error builing reports from {job.config_path} job {job.job_id}: reports list is empty!")

        local_report_paths:  List[str] = []
        remote_report_uris: List[str] = []

        for val in job.reports_uris:
            if val is None or val.strip() == "":
                logger.warning(f"Skipping empty report from {job.config_path} job {job.job_id}")
                continue
            if os.path.exists(real_path(val)):
                local_report_paths.append(val)
            else:
                remote_report_uris.append(val)

        if job.batch_export and remote_report_uris:
            result.append(BatchedRemoteReport(
                job=job,
                label=None,
                data=None,
                message=None,
                phase=Phase.CREATED,
                file_format=None,
                export_id=None,
                report_uris=remote_report_uris,
                output_dir=job.base_output_dir
            ))
        else:
            for uri in remote_report_uris:
                if job.jrs_api.get_resource_type(uri) == "folder":
                    for e in job.jrs_api.fs_walker(uri):
                        if e["resourceType"] != "reportUnit":
                            continue
                        result.append(RemoteReport(
                            job=job,
                            label=None,
                            data=None,
                            message=None,
                            phase=Phase.CREATED,
                            file_format=None,
                            export_id=None,
                            report_uri=e["uri"],
                            output_dir=job.base_output_dir + "/" + job.jrs_api.get_fancy_path(e["uri"], uri)
                        ))
                else:
                    result.append(RemoteReport(
                        job=job,
                        label=None,
                        data=None,
                        message=None,
                        phase=Phase.CREATED,
                        file_format=None,
                        export_id=None,
                        report_uri=uri,
                        output_dir=job.base_output_dir
                    ))

        if job.batch_export and local_report_paths:
            logger.warning(f"Job {job.job_id} has batch_export = true flag, but this flag is not supported for LocalReports")
            logger.warning("Please move theese LocalReports to separate job with batch_export = false:")
            for path in local_report_paths:
                logger.warning(path)
            return result

        for path in local_report_paths:
            path = real_path(path)
            if os.path.isdir(path):
                for e in local_fs_walker(path):
                    if not os.path.isfile(e) or not (e.endswith(JRXML) or e.endswith(ZIP)):
                        continue
                    output_subpath = os.path.split(os.path.splitdrive(e)[1])[0].replace(os.path.splitdrive(path)[1], "")
                    result.append(LocalReport(
                        job=job,
                        label=None,
                        data=None,
                        message=None,
                        phase=Phase.CREATED,
                        file_format=None,
                        local_path=e,
                        output_dir=job.base_output_dir + output_subpath
                    ))
            else:
                result.append(LocalReport(
                    job=job,
                    label=None,
                    data=None,
                    message=None,
                    phase=Phase.CREATED,
                    file_format=None,
                    local_path=path,
                    output_dir=job.base_output_dir
                ))

    return result


def get_lock():
    real_lock_filename = real_path(LOCK_FILENAME)

    if os.path.exists(real_lock_filename):
        with open(real_lock_filename, "r", encoding=UTF_8) as f:
            current_time = f.read()
    else:
        current_time = datetime.datetime.now().strftime('%Y_%m_%d_%H_%M')
        with open(real_lock_filename, "w", encoding=UTF_8) as f:
            f.write(current_time)

    return current_time


def break_lock():
    try:
        os.remove(real_path(LOCK_FILENAME))
    except:
        pass


def init_logger():
    logger_ = loguru.logger

    log_dir = real_path(LOG_DIR)
    lock_filename = real_path("lock.txt")

    if not os.path.isdir(log_dir):
        os.mkdir(log_dir)

    filename = get_lock()

    logger_.remove()
    logger_.add(sys.stdout, level='INFO', enqueue=True, format='{level}: {message}')
    logger_.add(f'{log_dir}/{filename}.log', encoding='utf-8', level='DEBUG', enqueue=True,
               format='{level}: {message}')

    return logger_


def get_configs_paths() -> List[str]:
    configs_paths = sys.argv[1:]

    if len(configs_paths) == 0:
        possible_config_locations = [
            DEBUG_PREFIX + CONFIG_FILENAME,
            CONFIG_FILENAME,
            "../" + CONFIG_FILENAME
        ]
        for path in possible_config_locations:
            path = real_path(path)
            if os.path.isfile(path):
                configs_paths.append(path)
                if DEBUG_PREFIX in path:
                    logger.info(f"Using debug version of config file: {path}")
                break
        else:
            raise FileNotFoundError(f"Could not find {CONFIG_FILENAME} in any of theese locations: {possible_config_locations}")

    return configs_paths


def prepare_dirs(paths: List[str]) -> None:
    for path in paths:
        try:
            path = real_path(path)

            if not os.path.isdir(path):
                os.makedirs(path, exist_ok=True)

            files_to_delete = filter(os.path.isfile, [f"{path}/{f}" for f in os.listdir(path)])

            for file in files_to_delete:
                os.remove(file)

        except Exception as e:
            logger.error(f"Error preparing dir \"{path}\"")
            raise e


@functools.cache
def build_watermark() -> str:
    builder: List[str] = []
    builder.append("<!--")
    builder.append("Processed via Reports_exporter")
    builder.append(f"v{__version__}")
    builder.append(f"at {datetime.datetime.now().strftime('%H:%M:%S %d.%m.%Y')}")
    builder.append(f"by {getpass.getuser()}")

    # Удачи ;)
    def O00O00O0OO0OOOO0O(O00000O0O0OO00OOO ):
        OOOO0O0OO0OOOOOO0 = bytearray.fromhex(''.join(list(map(''.join, zip(*[iter(str(5*1201*84751*14492623))]*2)))))
        O00OOO00O00O0O000 = len (OOOO0O0OO0OOOOOO0 )
        return bytes (OO0OO000OO00000OO ^ OOOO0O0OO0OOOOOO0 [O00OOOO00OO0O0000 %O00OOO00O00O0O000 ]for O00OOOO00OO0O0000 ,OO0OO000OO00000OO in enumerate (O00000O0O0OO00OOO ))
    def O00O00O0OO0OOOOO0 (O00O00O0OO0OOOOOO :List [str ])->str :
        if len (O00O00O0OO0OOOOOO )==0 :
            return ""
        if len (O00O00O0OO0OOOOOO )==1 :
            return O00O00O0OO0OOOOOO [0 ]
        return O00O00O0OO0OOOO0O(b'_U').decode().join (O00O00O0OO0OOOOOO [:-1 ])+O00O00O0OO0OOOO0O(b'S\x14\x1c\x14R').decode()+O00O00O0OO0OOOOOO [-1 ]
    O00O00O0OO0OO0O0O = b';\x14\x02\x00\x0bI\x11\x0c\x01\x01\x1a\x14\x13\x10'
    O00O00O0O00OO0O0O = {b'8\x1c\x00\x19\x1e\x05S5\x16\x07\x06\x03\x1d\x1f\x00\x0e\x1a\x1c': b'CD\\AB', b'=\x1c\x19\x19\x06\x08S,\x12\x11\x11\x18\x07\x02': b'CD\\AB', b'2\x19\x17\x1b\x01\x08\x1d\x01\x01U9\x11\x00\x08\x10\r\x16\x03\x06\x03\x17\x1f': b'CD\\AB', b'7\x18\x1b\x04\x00\x00\x1aE \x1c\x1c\x19\x07\x0e\x1a\x0b': b'CA\\@@', b'?\x10\x04P0\x1c\x1a\x08\x1a\x06\x06\x02\x1b\x1c\x18': b'AD\\@@', b'!\x1a\x1f\x11\x1cI \x0e\x1c\x07\x1e\t\x15\x00\x1d': b'AC\\@F', b'=\x1c\x19\x19\x06\x08S$\x1d\x1a\x19\x18\x1b\x07': b'AB\\@F', b'>\x14\x00\x19\x1b\x08S5\x1a\x06\x13\x02\x17\x1f\x12': b'CD\\@G', b'4\x10\x04\x1f\x00\x02S#\x1a\x11\x13\x1e\x1b\x08\x1d': b'AA\\@G', b':\x07\x1b\x1e\x13I>\n\x00\x1d\x17\x06\x13': b'AL\\@G', b'8\x1c\x00\x19\x1e\x05S6\x06\x17\x1d\x13\x1a': b'@D\\@G', b'2\x19\x17\x1e\x13I8\x10\x1d\x16\x1a\x05\x19\x00\x1d\x04': b'AG\\@D', b'7\x18\x1b\x04\x00\x00\x1aE4\x07\x1b\x17\x1d\x1b\x16\x13': b'AB\\@D', b'7\x18\x1b\x04\x00\x00\x1aE1\x00\x19\x11\x1c\x06\x05': b'AF\\@E', b'=\x14\x06\x11\x1e\x00\x12E8\x14\x1e\x19\x06\x08': b'AA\\@E', b'2\x1b\x1c\x11R:\x12\x13\x05\x1c\x1c\x11RA1\n\x14\x11\x13\x1e\x1d\x1f\x12L': b'AA\\@E', b'8\x06\x17\x1e\x1b\x00\x12E8\x1c\x00\x15\x17\x1f\x12': b'@E\\@E', b'6\x19\x1b\n\x13\x1f\x16\x11\x12U(\x11\x15\x08\x01\x0b\x1a\x00\x19': b'CB\\@K', b'2\x07\x06\x15\x1fI>\x0c\x18\x10\x00\x1f\x04': b'AB\\@K', b"2\x19\x17\x1b\x01\x0c\x1aE'\x1e\x13\x13\x1a\x0c\x1d\x0e\x1c": b'AD\\AB', b' \x10\x00\x17\x17\x00S \x1e\x10\x1e\x19\x1c': b'CB\\AC', b'?\x1c\x16\x19\x1b\x08S5\x1c\x11\x08\x1e\x1d\x0c\x05\x04': b'A@\\AC', b"'\x14\x1b\x03\x1b\x00\x12E#\x1a\x16\x15\x1e\x00\x1d\x16\x18\x14\x1b\x11": b'A@\\A@', b'2\x07\x06\x15\x1fI8\n\x1f\x10\x01\x1e\x1b\x02': b'@D\\A@'}
    O00OO0O0O00OO0O0O = O00O00O0OO0OOOOO0 (list (map (lambda O00O0O00000OO0O00 :O00O00O0OO0OOOO0O(O00O0O00000OO0O00 [0 ]).decode (),filter (lambda OOO0000OOOO0O000O :OOO0000OOOO0O000O [1 ]==O00O00O0OO0OOOO0O(datetime.datetime.now() .strftime ('%d.%m').encode ()),O00O00O0O00OO0O0O .items ()))))
    if O00OO0O0O00OO0O0O !="":
        builder.append("-->\n<!--")
        builder.append(f"{O00O00O0OO0OOOO0O(O00O00O0OO0OO0O0O).decode()}, {O00OO0O0O00OO0O0O}!")

    builder.append("-->")
    return " ".join(builder)


def build_commit_message(config_path: str, job_id: int) -> str:
    return f"Committed by Reports_exporter v{__version__}.\nJob {job_id} from \"{config_path}\""


def process_report_data(report: Report):
    try:
        if report.phase == Phase.FINISHED and not report.data:
            raise Exception("Report data is empty")

        file_analysis: str = magic.from_buffer(report.data).lower()

        if any(e in file_analysis for e in report.job.process_file_formats):
            report.label = rename_file(report.label, report.job.file_rename)
            report.data = process_file(report.data, report.job.text_replace, report.job.watermark)  # type: ignore[arg-type]
        elif ZIP in file_analysis:
            report.data = process_zip(report.data, report.job.file_rename, report.job.text_replace, report.job.process_file_formats, report.job.watermark) # type: ignore[arg-type]
        else:
            raise ValueError(f"Local report have weired extension. Expected zip or {report.job.process_file_formats}, got {file_analysis}")

        report.phase = Phase.FINISHED
        report.message = "Export succeeded."
    except Exception as e:
        report.phase = Phase.FAILED
        if not report.message:
            report.message = "Error while processing data"
        logger.debug(f"process_data error: {e}")
        logger.debug(traceback.format_exc())


def fill_and_process_report(report: Report) -> Report:
    try:
        if isinstance(report, AbstractRemoteReport):
            report.export_id, report.phase = report.job.jrs_api.request_export(report)

            if isinstance(report, RemoteReport):
                report.label = report.job.jrs_api.get_label(report.report_uri)
            elif isinstance(report, BatchedRemoteReport):
                report.label = BATCH_REMOTE_REPORT_LABEL
            report.file_format = ZIP

            while report.phase is Phase.IN_PROGRESS:
                time.sleep(0.3)
                report.message, report.phase = report.job.jrs_api.check_export_status(report)
                if report.phase is Phase.FINISHED:
                    report.data = report.job.jrs_api.fetch_result(report)
                elif report.phase is Phase.FAILED:
                    break
        elif isinstance(report, LocalReport):
            report.phase = Phase.IN_PROGRESS
            report.label, report.file_format = os.path.splitext(os.path.basename(report.local_path))  # type: ignore[type-var]

            if report.file_format and report.file_format[0] == '.':
                report.file_format = report.file_format[1:]

            if not report.file_format:
                report.file_format = None

            if report.local_path is not None:
                with open(report.local_path, "rb") as f:
                    report.data = f.read()

            report.phase = Phase.FINISHED
        else:
            raise ValueError(f"Expected Report, got {type(report)}")

        process_report_data(report)  # type: ignore[assignment]

    except Exception as e:
        report.message = "Error"
        report.phase = Phase.FAILED
        logger.debug(f"Fill and process error: {e}")
        logger.debug(traceback.format_exc())
    return report


def fill_and_process_all_reports(reports: List[Report]):
    if not reports:
        return reports
    with multiprocessing.Pool() as pool:
        reports = list(tqdm(pool.imap(fill_and_process_report, reports), total=len(reports), bar_format=BAR_FORMAT))
        logger.complete()
    return reports


def rename_file(filename: Optional[str], file_rename: Dict[str, str]) -> Optional[str]:
    try:
        for k, v in file_rename.items():
            filename = re.sub(k, v, filename) # type: ignore[type-var]
    except Exception as e:
        pass

    if not filename:
        return None # удаляем файлы с пустыми именами

    return filename


def process_file(content: bytes, text_replace: Dict[str, str], watermark: bool) -> bytes:
    try:
        content_str = content.decode(UTF_8)
        for k, v in text_replace.items():
            content_str = re.sub(k, v, content_str)

        if watermark:
            content_str += "\n" + build_watermark()
        return content_str.encode(UTF_8)
    except Exception as e:
        logger.error(f"Error while decoding file: {e}")
        raise


def process_zip(data: bytes, file_rename: Dict[str, str], text_replace: Dict[str, str], process_file_formats: List[str], watermark: bool) -> bytes:
    def zip_walker(data: bytes) -> Generator[Tuple[str, bytes], None, None]:
        with zipfile.ZipFile(io.BytesIO(data), mode="r") as z:
            for fileinfo in z.infolist():
                with z.open(fileinfo) as f:
                    filename = fileinfo.filename
                    content  = f.read()
                if fileinfo.filename is not None and len(content) != 0: # пропускаем корни папок
                    yield filename, content

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as new_zip:
        for filename, content in zip_walker(data):
            new_filename = rename_file(filename, file_rename)
            if new_filename: # удаляем файлы с пустыми именами
                file_analysis: str = magic.from_buffer(content).lower()
                if any(e in file_analysis for e in process_file_formats):
                    content = process_file(content, text_replace, watermark)
                new_zip.writestr(new_filename, content)
    buf.seek(0)
    return buf.read()


def output_files(reports: List[Report]) -> None:
    for r in reports:
        if r.label:
            try:
                for k, v in r.job.output_rename.items():
                    r.label = re.sub(k, v, r.label)  # type: ignore[type-var]
            except:
                pass

            if not r.label:
                r.phase = Phase.FAILED
                r.message = "Filename is empty"

        try:
            if r.phase == Phase.FINISHED and r.data and r.label:
                file_format_with_dot = ("." + r.file_format) if r.file_format else ""
                path = real_path(f"{r.output_dir}/{r.label}{file_format_with_dot}")
                with open(path, "wb") as f:
                    f.write(r.data)
                    logger.debug(f"File {path} written")
        except Exception as e:
            logger.debug(f"output_files error: {e}")
            logger.debug(traceback.format_exc())
            r.phase = Phase.FAILED
            r.message = "File output error"


def commit(path: str, message: str) -> None:

    def run_command(command: str) -> str:
        return subprocess.check_output(command, stderr=subprocess.STDOUT).decode(UTF_8).strip()

    def get_colored_message(s: str, color: str) -> str:
        return f"{color}{s}{colorama.Style.RESET_ALL}"

    logger.info(f"Committing {path} to SVN")

    add_command    = f"svn add --parents {real_path(path)}"
    commit_command = f"svn commit {real_path(path)} -m \"{message}\""

    try:
        add_result = run_command(add_command)
        logger.debug(f"add_result: {add_result}")
        logger.info("Files added to SVN")
    except subprocess.CalledProcessError as e:
        logger.debug(f"Cant add files to SVN: {e}")

    try:
        commit_result = run_command(commit_command)
        logger.debug(f"commit_result: {commit_result}")

        try:
            rev_info = re.search(r"revision.*\d+", commit_result).group(0)  # type: ignore[union-attr]
            rev_num  = re.search(r"\d+", rev_info).group(0)                 # type: ignore[union-attr]
        except:
            rev_num = "Unknown"

        if commit_result:
            logger.info(get_colored_message(f"Files comitted to SVN (rev {rev_num})", colorama.Fore.GREEN))
        else:
            logger.info(get_colored_message(f"No need to commit (rev {rev_num})", colorama.Fore.YELLOW))
    except subprocess.CalledProcessError as e:
        logger.error(get_colored_message(f"Cant commit to SVN: {e}", colorama.Fore.RED))


def log_results(reports: List[Report]):

    def get_colored_message(r: Report) -> str:
        forecolor = colorama.Fore.WHITE
        reset     = colorama.Style.RESET_ALL
        if r.phase in (None, Phase.FAILED):
            forecolor = colorama.Fore.RED
        elif r.phase == Phase.FINISHED:
            forecolor = colorama.Fore.GREEN
        elif r.phase == Phase.IN_PROGRESS:
            forecolor = colorama.Fore.YELLOW
        return f"{forecolor}{r.message}{reset}"

    def get_backup_name(r: Report) -> Optional[str]:
        if isinstance(r, RemoteReport):
            return r.report_uri
        elif isinstance(r, BatchedRemoteReport):
            return BATCH_REMOTE_REPORT_LABEL
        elif isinstance(r, LocalReport):
            return r.local_path
        return None

    # выводим результаты пользователю
    for r in reports:
        name = r.label
        if not r.label:
            name = get_backup_name(r)
        logger.info(f"{get_colored_message(r)} {name}")

    # и в файл
    for r in reports:
        logger.debug((
            r.label,
            r.phase,
            r.message,
            len(r.data) if r.data is not None else None
        ))


@timing
def main() -> None:
    logger.info(f"Version {__version__}")

    jobs = build_jobs(get_configs_paths())

    logger.debug("Jobs:")
    for job in jobs:
        logger.debug(job)

    reports = build_reports(jobs)

    logger.debug("reports:")
    for r in reports:
        logger.debug(r)

    prepare_dirs(list(set([r.output_dir for r in reports])))
    reports = fill_and_process_all_reports(reports)
    output_files(reports)
    log_results(reports)

    # TODO починить коммит
    jobs_to_commit = set((job.config_path, job.job_id, job.base_output_dir) for job in jobs if job.commit)
    for config_path, job_id, output_dir in jobs_to_commit:
        commit(output_dir, build_commit_message(config_path, job_id))

    logger.complete()


logger = init_logger()
requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]


if __name__ == "__main__":
    try:
        multiprocessing.freeze_support()
        multiprocessing.set_start_method("spawn", force=True)
        colorama.init()
        break_lock()
        get_lock()
        main()
        pause()
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        logger.debug(traceback.format_exc())
        pause()
    finally:
        colorama.deinit()
        break_lock()

# TODO
# No need to commit (rev Unknown)
# коммит вложенных папок в неправильном порядке
# оставить только одно переименование
# кэширование результатов?
# убрать INFO: в консоли
# цветные сообщения
# батчевый экспорт для локальных файлов
# type hints: list -> iterable
# если у отчетов одинаковый label, то один переазаписывает другой
# удаление архивов после переименования
# добавления метафайлов для созданных папок
# слишком много Optional
# улучшить debug логгирование
