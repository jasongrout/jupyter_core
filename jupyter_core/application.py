# encoding: utf-8
"""
A base Application class for Jupyter applications.

All Jupyter applications should inherit from this.
"""

# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.

from __future__ import print_function

from copy import deepcopy
import logging
import os
import sys

try:
    # py3
    from shutil import which
except ImportError:
    from .utils.shutil_which import which

from traitlets.config.application import Application, catch_config_error
from traitlets.config.loader import ConfigFileNotFound
from traitlets import Unicode, Bool, List

from ipython_genutils.path import ensure_dir_exists
from ipython_genutils import py3compat

from .paths import (
    jupyter_config_dir, jupyter_data_dir, jupyter_runtime_dir,
    jupyter_path, jupyter_config_path,
)

# aliases and flags

base_aliases = {
    'log-level' : 'Application.log_level',
    'config' : 'JupyterApp.config_file',
}

base_flags = {
    'debug': ({'Application' : {'log_level' : logging.DEBUG}},
            "set log level to logging.DEBUG (maximize logging output)"),
    'generate-config': ({'JupyterApp': {'generate_config': True}},
        "generate default config file"),
    'y': ({'JupyterApp': {'answer_yes': True}},
        "Answer yes to any questions instead of prompting."),
}

class NoStart(Exception):
    """Exception to raise when an application shouldn't start"""

class JupyterApp(Application):
    """Base class for Jupyter applications"""
    name = 'jupyter' # override in subclasses
    description = "A Jupyter Application"
    
    aliases = base_aliases
    flags = base_flags
    
    def _log_level_default(self):
        return logging.INFO
    
    jupyter_path = List(Unicode())
    def _jupyter_path_default(self):
        return jupyter_path()
    
    config_dir = Unicode()
    
    def _config_dir_default(self):
        return jupyter_config_dir()
    
    @property
    def config_file_paths(self):
        path = jupyter_config_path()
        if self.config_dir not in path:
            path.insert(0, self.config_dir)
        path.insert(0, py3compat.getcwd())
        return path
    
    data_dir = Unicode()
    
    def _data_dir_default(self):
        d = jupyter_data_dir()
        ensure_dir_exists(d, mode=0o700)
        return d
    runtime_dir = Unicode()
    
    def _runtime_dir_default(self):
        rd = jupyter_runtime_dir()
        ensure_dir_exists(rd, mode=0o700)
        return rd
    
    def _runtime_dir_changed(self, new):
        ensure_dir_exists(new, mode=0o700)
    
    generate_config = Bool(False, 
        help="""Generate default config file."""
    ).tag(config=True)
    
    config_file_name = Unicode(
        help="Specify a config file to load."
    ).tag(config=True)
    def _config_file_name_default(self):
        if not self.name:
            return ''
        return self.name.replace('-','_') + u'_config'
    
    config_file = Unicode(
        help="""Full path of a config file.""",
    ).tag(config=True)
    
    answer_yes = Bool(False, 
        help="""Answer yes to any prompts."""
    ).tag(config=True)
    
    def write_default_config(self):
        """Write our default config to a .py config file"""
        if self.config_file:
            config_file = self.config_file
        else:
            config_file = os.path.join(self.config_dir, self.config_file_name + '.py')
        
        if os.path.exists(config_file) and not self.answer_yes:
            answer = ''
            def ask():
                prompt = "Overwrite %s with default config? [y/N]" % config_file
                try:
                    return input(prompt).lower() or 'n'
                except KeyboardInterrupt:
                    print('') # empty line
                    return 'n'
            answer = ask()
            while not answer.startswith(('y', 'n')):
                print("Please answer 'yes' or 'no'")
                answer = ask()
            if answer.startswith('n'):
                return
        
        config_text = self.generate_config_file()
        if isinstance(config_text, bytes):
            config_text = config_text.decode('utf8')
        print("Writing default config to: %s" % config_file)
        ensure_dir_exists(os.path.abspath(os.path.dirname(config_file)), 0o700)
        with open(config_file, mode='w') as f:
            f.write(config_text)
    
    def migrate_config(self):
        """Migrate config/data from IPython 3"""
        if os.path.exists(os.path.join(self.config_dir, 'migrated')):
            # already migrated
            return

        from .migrate import get_ipython_dir, migrate
        
        ipdir = get_ipython_dir()
        # No IPython dir, nothing to migrate
        if not os.path.exists(ipdir):
            return

        migrate()

    def load_config_file(self, suppress_errors=True):
        """Load the config file.

        By default, errors in loading config are handled, and a warning
        printed on screen. For testing, the suppress_errors option is set
        to False, so errors will make tests fail.
        """
        self.log.debug("Searching %s for config files", self.config_file_paths)
        base_config = 'jupyter_config'
        try:
            super(JupyterApp, self).load_config_file(
                base_config,
                path=self.config_file_paths,
            )
        except ConfigFileNotFound:
            # ignore errors loading parent
            self.log.debug("Config file %s not found", base_config)
            pass

        if self.config_file:
            path, config_file_name = os.path.split(self.config_file)
        else:
            path = self.config_file_paths
            config_file_name = self.config_file_name

            if not config_file_name or (config_file_name == base_config):
                return

        try:
            super(JupyterApp, self).load_config_file(
                config_file_name,
                path=path
            )
        except ConfigFileNotFound:
            self.log.debug("Config file not found, skipping: %s", config_file_name)
        except Exception:
            # For testing purposes.
            if not suppress_errors:
                raise
            self.log.warn("Error loading config file: %s" %
                            config_file_name, exc_info=True)

    # subcommand-related
    def _find_subcommand(self, name):
        name = '{}-{}'.format(self.name, name)
        return which(name)
    
    @property
    def _dispatching(self):
        """Return whether we are dispatching to another command
        
        or running ourselves.
        """
        return bool(self.generate_config or self.subapp or self.subcommand)
    
    subcommand = Unicode()
    
    @catch_config_error
    def initialize(self, argv=None):
        # don't hook up crash handler before parsing command-line
        if argv is None:
            argv = sys.argv[1:]
        if argv:
            subc = self._find_subcommand(argv[0])
            if subc:
                self.argv = argv
                self.subcommand = subc
                return
        self.parse_command_line(argv)
        cl_config = deepcopy(self.config)
        if self._dispatching:
            return
        self.migrate_config()
        self.load_config_file()
        # enforce cl-opts override configfile opts:
        self.update_config(cl_config)
    
    
    def start(self):
        """Start the whole thing"""
        if self.subcommand:
            os.execv(self.subcommand, [self.subcommand] + self.argv[1:])
            raise NoStart()
        
        if self.subapp:
            self.subapp.start()
            raise NoStart()
        
        if self.generate_config:
            self.write_default_config()
            raise NoStart()
    
    @classmethod
    def launch_instance(cls, argv=None, **kwargs):
        """Launch an instance of a Jupyer Application"""
        try:
            return super(JupyterApp, cls).launch_instance(argv=argv, **kwargs)
        except NoStart:
            return

if __name__ == '__main__':
    JupyterApp.launch_instance()
