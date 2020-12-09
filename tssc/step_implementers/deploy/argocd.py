"""
Step Implementer for the deploy step for ArgoCD.

Step Configuration
------------------
Step configuration expected as input to this step.
Could come from either configuration file or
from runtime configuration.

| Configuration Key         | Required?          | Default              | Description
|---------------------------|--------------------|----------------------|---------------------------
| `argocd-username`         | True               |                      | Username for accessing the
                                                 |                      | ArgoCD API
| `argocd-password`         | True               |                      | Password for accessing the
                                                 |                      | ArgoCD API
| `argocd-api`              | True               |                      | The ArgoCD API endpoint
| `argocd-auto-sync`        | True               | 'false'              | If set to false, argo cd
|                           |                    |                      | will sync only if
|                           |                    |                      | explicitly told to do so
|                           |                    |                      | via the UI or CLI.
|                           |                    |                      | Otherwise it will sync if
|                           |                    |                      | the repo contents have
|                           |                    |                      | changed
| `helm-config-repo`        | True               |                      | The repo containing the
|                           |                    |                      | helm chart definiton
| `values-yaml-directory`   | True               | ./cicd/Deployment/   | Directory containing jinja
|                           |                    |                      | templates
| `value-yaml-template`     | True               | values.yaml.j2       | Name of the values yaml
|                           |                    |                      | jinja file
| `argocd-sync-timeout-`    | True               | 60                   | Number of seconds to wait
| `seconds`                 |                    |                      | for argocd to sync updates
| `kube-api-uri`            | True               | https://kubernetes.  | k8s API endpoint
|                           |                    | default.svc          |
| `kube-api-token`          | False              |                      | k8s API token. This is
|                           |                    |                      | used to add an external
|                           |                    |                      | k8s cluster into argocd.
|                           |                    |                      | It is required if the
|                           |                    |                      | cluster has not already
|                           |                    |                      | been added to ArgoCD. The
|                           |                    |                      | token should be persistent
|                           |                    |                      | (.e.g, a service account
|                           |                    |                      | token) and have cluster
|                           |                    |                      | admin access.
| `insecure-skip-tls-verify`| True               | 'true'               | Whether or not to skip
|                           |                    |                      | tls verification when
|                           |                    |                      | authenticating to an
|                           |                    |                      | external k8s cluster.
|                           |                    |                      | Used when a new cluster
|                           |                    |                      | is registered with argocd
| `argocd-helm-chart-path`  | True               | ./                   | Directory containing the
|                           |                    |                      | helm chart definition
| `git-email`               | True               |                      | Git email for commit
| `git-friendly-name`       | True               | TSSC                 | Git name for commit
| `git-username`            | False              |                      | If the helm config repo
|                           |                    |                      | is accessed via http(s)
|                           |                    |                      | this must be supplied
| `git-password`            | False              |                      | If the helm config repo
                            |                    |                      | is accessed via http(s)
                            |                    |                      | this must be supplied

Expected Previous Step Results
------------------------------
Results expected from previous steps that this step may require.

| Step Name              | Result Key      | Description
|------------------------|-----------------|------------
| `tag-source`           | `tag`           | The git tag to apply to the config repo
| `push-container-image` | `image-url`     | The image url to use in the deployment
| `push-container-image` | `container-image-version` | The image version use in the deployment

Results
-------
Results output by this step.

| Result Key            | Description
|-----------------------|------------
| `argocd-app-name`     | The argocd app name that was created or updated
| `deploy-endpoint-url` | The endpoint url for the deployed app
| `config-repo-git-tag` | The git tag applied to the configuration repo for deployment
| `argocd-result-set`   | The generated yml file used for deployment.

"""
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime

import sh
from jinja2 import Environment, FileSystemLoader
from tssc import StepImplementer
from tssc.config import ConfigValue
from tssc.exceptions import StepRunnerException
from tssc.step_result import StepResult

DEFAULT_CONFIG = {
    'values-yaml-directory': './cicd/Deployment',
    'values-yaml-template': 'values.yaml.j2',
    'argocd-sync-timeout-seconds': 60,
    'argocd-auto-sync': 'false',
    'insecure-skip-tls-verify': 'true',
    'kube-api-uri': 'https://kubernetes.default.svc',
    'argocd-helm-chart-path': './',
    'git-friendly-name': 'TSSC'
}

REQUIRED_CONFIG_OR_PREVIOUS_STEP_RESULT_ARTIFACT_KEYS = [
    'argocd-username',
    'argocd-password',
    'argocd-api',
    'helm-config-repo',
    'git-email',
    'container-image-uri',
    'tag'
]

GIT_AUTHENTICATION_CONFIG = {
    'git-username': None,
    'git-password': None
}

KUBE_LABEL_NOT_SAFE_CHARS_REGEX = r"[^a-zA-Z0-9\-_\.]"
KUBE_LABEL_NOT_SAFE_BEGINING_END_CHARS_REGEX = r"^[^a-zA-Z0-9]*|[^a-zA-Z0-9]*$"
KUBE_LABEL_MAX_LENGTH = 52
KUBE_LABEL_REPLACEMENT_CHAR = '-'


class ArgoCD(StepImplementer):
    """ StepImplementer for the deploy step for ArgoCD.
    """

    @staticmethod
    def step_implementer_config_defaults():
        """
        Getter for the StepImplementer's configuration defaults.

        Returns
        -------
        dict
            Default values to use for step configuration values.

        Notes
        -----
        These are the lowest precedence configuration values.
        """
        return DEFAULT_CONFIG

    @staticmethod
    def _required_config_or_result_keys():
        """Getter for step configuration or previous step result artifacts that are required before
        running this step.

        See Also
        --------
        _validate_required_config_or_previous_step_result_artifact_keys

        Returns
        -------
        array_list
            Array of configuration keys or previous step result artifacts
            that are required before running the step.
        """
        return REQUIRED_CONFIG_OR_PREVIOUS_STEP_RESULT_ARTIFACT_KEYS

    def _validate_required_config_or_previous_step_result_artifact_keys(self):
        """Validates that the required configuration keys or previous step result artifacts
        are set and have valid values.

        Validates that:
        * required configuration is given
        * either both git-username and git-password are set or neither.

        Raises
        ------
        StepRunnerException
            If step configuration or previous step result artifacts have invalid required values
        """
        super()._validate_required_config_or_previous_step_result_artifact_keys()

        # ensure either both git-username and git-password are set or neither
        runtime_auth_config = {}
        for auth_config_key in GIT_AUTHENTICATION_CONFIG:
            runtime_auth_config_value = self.get_value(auth_config_key)

            if runtime_auth_config_value is not None:
                runtime_auth_config[auth_config_key] = runtime_auth_config_value

        if (any(element in runtime_auth_config for element in GIT_AUTHENTICATION_CONFIG)) and \
                (not all(element in runtime_auth_config for element in GIT_AUTHENTICATION_CONFIG)):
            raise StepRunnerException(
                "Either 'git-username' or 'git-password 'is not set. Neither or both must be set."
            )

    def _run_step(self):  # pylint: disable=too-many-locals
        """Runs the step implemented by this StepImplementer.

        Returns
        -------
        StepResult
            Object containing the dictionary results of this step.
        """
        step_result = StepResult.from_step_implementer(self)

        try:
            sh.argocd.login(  # pylint: disable=no-member
                self.get_value('argocd-api'),
                '--username=' + self.get_value('argocd-username'),
                '--password=' + self.get_value('argocd-password'),
                '--insecure',
                _out=sys.stdout,
                _err=sys.stderr
            )
        except sh.ErrorReturnCode as error:
            raise RuntimeError("Error logging in to ArgoCD: {all}".format(all=error)) from error

        kube_api = self.get_value('kube-api-uri')
        # If the cluster is an external cluster and an api token was provided,
        # add the cluster to ArgoCD
        if kube_api != DEFAULT_CONFIG['kube-api-uri']:
            context_name = f'{kube_api}-context'
            kubeconfig = """
current-context: {context}
apiVersion: v1
clusters:
- cluster:
    insecure-skip-tls-verify: {skip_tls}
    server: {kube_api}
  name: default-cluster
contexts:
- context:
    cluster: default-cluster
    user: default-user
  name: {context}
kind: Config
preferences:
users:
- name: default-user
  user:
    token: {kube_token}
            """.format(context=context_name,
                       kube_api=kube_api,
                       kube_token=self.get_value('kube-api-token'),
                       skip_tls=str(self.get_value('insecure-skip-tls-verify').lower()))

            with tempfile.NamedTemporaryFile(buffering=0) as temp_file:
                temp_file.write(bytes(kubeconfig, 'utf-8'))
                try:
                    sh.argocd.cluster.add(  # pylint: disable=no-member
                        '--kubeconfig',
                        temp_file.name,
                        context_name,
                        _out=sys.stdout,
                        _err=sys.stderr
                    )
                except sh.ErrorReturnCode as error:
                    raise RuntimeError("Error adding cluster to ArgoCD: {cluster}".format(
                        cluster=kube_api)) from error

        helm_chart_path = self.get_value('argocd-helm-chart-path')
        values_file_name = f'values-{self.environment}.yaml' if self.environment else 'values.yaml'
        # NOTE:
        #   While helm supports values files being anywhere, ArgoCD only supports values files
        #   within the specified --path for the new applciation
        values_file_repo_relative_path = os.path.join(helm_chart_path, values_file_name)

        # NOTE: In this block the reference app config repo is cloned and checked out to a temp
        #       directory so that it can update the values.yml based on values.yaml.j2 template.
        #       It then pushes these changes to a respective branch that has the same name as the
        #       reference app as well as tags the branch.
        with tempfile.TemporaryDirectory() as repo_directory:

            git_url = self.get_value('helm-config-repo')
            repo_branch = self._get_repo_branch()

            try:
                sh.git.clone( # pylint: disable=no-member
                    git_url,
                    repo_directory,
                    _out=sys.stdout,
                    _err=sys.stderr
                )

                try:
                    sh.git.checkout(  # pylint: disable=no-member
                        repo_branch,
                        _cwd=repo_directory,
                        _out=sys.stdout,
                        _err=sys.stderr
                    )

                except sh.ErrorReturnCode:
                    sh.git.checkout(
                        '-b',
                        repo_branch,
                        _cwd=repo_directory,
                        _out=sys.stdout,
                        _err=sys.stderr
                    )

                self._update_values_yaml(repo_directory, values_file_repo_relative_path)

                git_commit_msg = 'Configuration Change from TSSC Pipeline. Repository: ' + \
                                 '{repo}'.format(repo=git_url)

                sh.git.config( # pylint: disable=no-member
                    '--global',
                    'user.email',
                    self.get_value('git-email'),
                    _out=sys.stdout,
                    _err=sys.stderr
                )

                sh.git.config( # pylint: disable=no-member
                    '--global',
                    'user.name',
                    self.get_value('git-friendly-name'),
                    _out=sys.stdout,
                    _err=sys.stderr
                )

                sh.git.add( # pylint: disable=no-member
                    values_file_repo_relative_path,
                    _cwd=repo_directory,
                    _out=sys.stdout,
                    _err=sys.stderr
                )

                sh.git.commit( # pylint: disable=no-member
                    '-am',
                    git_commit_msg,
                    _cwd=repo_directory,
                    _out=sys.stdout,
                    _err=sys.stderr
                )

                sh.git.status( # pylint: disable=no-member
                    _cwd=repo_directory,
                    _out=sys.stdout,
                    _err=sys.stderr
                )

            except sh.ErrorReturnCode as error:  # pylint: disable=undefined-variable
                raise RuntimeError("Error invoking git: {all}".format(all=error)) from error

            self._git_tag_and_push(repo_directory)

            argocd_app_name = self._get_app_name()

            try:
                sh.argocd.app.get(  # pylint: disable=no-member
                    argocd_app_name,
                    _out=sys.stdout,
                    _err=sys.stderr
                )
            except sh.ErrorReturnCode_1:  # pylint: disable=undefined-variable, no-member
                print('No app found, creating a new app...')

            sync_policy = 'automated' if str(
                self.get_value('argocd-auto-sync')).lower() == 'true' else 'none'

            sh.argocd.app.create(  # pylint: disable=no-member
                argocd_app_name,
                '--repo=' + git_url,
                '--revision=' + repo_branch,
                '--path=' + helm_chart_path,
                '--dest-server=' + self.get_value('kube-api-uri'),
                '--dest-namespace=' + argocd_app_name,
                '--sync-policy=' + sync_policy,
                '--values=' + values_file_name,
                _out=sys.stdout,
                _err=sys.stderr
            )

            sh.argocd.app.sync(  # pylint: disable=no-member
                '--timeout',
                self.get_value('argocd-sync-timeout-seconds'),
                argocd_app_name,
                _out=sys.stdout,
                _err=sys.stderr
            )

            sh.argocd.app.wait(  # pylint: disable=no-member
                '--timeout',
                self.get_value('argocd-sync-timeout-seconds'),
                '--health',
                argocd_app_name,
                _out=sys.stdout,
                _err=sys.stderr
            )

            # NOTE: Creating a file to pass to the next step
            manifest_file = self.write_working_file(
                'deploy_argocd_manifests.yml',
                b''
            )

            sh.argocd.app.manifests(  # pylint: disable=no-member
                argocd_app_name,
                _out=manifest_file,
                _err=sys.stderr
            )

            step_result.add_artifact(
                name='argocd-app-name',
                value=argocd_app_name
            )
            step_result.add_artifact(
                name='deploy-endpoint-url',
                value=f'http://{self._get_endpoint_url()}'
            )
            step_result.add_artifact(
                name='config-repo-git-tag',
                value=self._get_tag(repo_directory)
            )
            step_result.add_artifact(
                name='argocd-result-set',
                value=manifest_file
            )

        return step_result

    def _get_image_version(self):
        image_version = self.get_value('container-image-version')
        if image_version is None:
            image_version = 'latest'
            print('No image version found in metadata. Using latest.')
        return image_version

    def _update_values_yaml(self, repo_directory, values_file_repo_relative_path):  # pylint: disable=too-many-locals
        env = Environment(
            loader=FileSystemLoader(self.get_value('values-yaml-directory')),
            trim_blocks=True,
            lstrip_blocks=True
        )

        argocd_app_name = self._get_app_name()
        version = self._get_image_version()
        container_image_uri = self.get_value('container-image-uri')
        timestamp = str(datetime.now())
        repo_branch = self._get_repo_branch()
        endpoint_url = self._get_endpoint_url()
        jinja_runtime_step_config = {'container_image_uri': container_image_uri,
                                     'image_version': version,
                                     'timestamp': timestamp,
                                     'repo_branch': repo_branch,
                                     'deployment_namespace': argocd_app_name,
                                     'endpoint_url': endpoint_url}

        copy_of_runtime_step_config = ConfigValue.convert_leaves_to_values(
            self.get_copy_of_runtime_step_config()
        )
        for key in copy_of_runtime_step_config:
            jinja_runtime_step_config[key.replace('-', '_')] = copy_of_runtime_step_config[key]

        template = env.get_template(self.get_value('values-yaml-template'))

        rendered_values_file = self.write_working_file(
            'values.yml',
            bytes(template.render(jinja_runtime_step_config), 'utf-8')
        )

        try:
            shutil.copyfile(
                rendered_values_file,
                os.path.join(repo_directory, values_file_repo_relative_path)
            )
        except (shutil.SameFileError, OSError, IOError) as error:
            raise RuntimeError("Error copying {values_file} file: {all}".format(
                values_file=values_file_repo_relative_path, all=error)) from error

    def _get_tag(self, repo_directory):
        """TODO: doc me
        """
        tag = self.get_value('tag')
        if tag is None:
            tag = 'latest'
            print('No version found in metadata. Using latest.')

        commit_tag = sh.git( # pylint: disable=no-member
            'rev-parse',
            '--short',
            'HEAD',
            _cwd=repo_directory
        ).rstrip()

        full_tag = "{tag}.{commit_tag}".format(tag=tag, commit_tag=commit_tag)

        return full_tag

    def _git_tag_and_push(self, repo_directory):
        username = None
        password = None

        if self.has_config_value(GIT_AUTHENTICATION_CONFIG):
            if (self.get_value('git-username') \
                    and self.get_value('git-password')):
                username = self.get_value('git-username')
                password = self.get_value('git-password')
            else:
                raise ValueError(
                    'Both username and password must have ' \
                    'non-empty value in the runtime step configuration'
                )
        else:
            print('No username/password found, assuming ssh')
        git_url = self.get_value('helm-config-repo')
        if git_url.startswith('http://'):
            if username and password:
                self._git_push(repo_directory,
                               'http://{username}:{password}@{url}'.format(
                                   username=username,
                                   password=password,
                                   url=git_url[7:]))
            else:
                raise ValueError(
                    'For a http:// git url, you need to also provide ' \
                    'username/password pair'
                )
        elif git_url.startswith('https://'):
            if username and password:
                self._git_push(repo_directory,
                               'http://{username}:{password}@{url}'.format(
                                   username=username,
                                   password=password,
                                   url=git_url[8:]))

            else:
                raise ValueError(
                    'For a https:// git url, you need to also provide ' \
                    'username/password pair'
                )
        else:
            self._git_push(repo_directory, None)

    def _git_push(self, repo_directory, url=None):

        git_push = sh.git.push.bake(url) if url else sh.git.push

        try:
            git_push(
                _out=sys.stdout,
                _cwd=repo_directory
            )

            tag = self._get_tag(repo_directory)
            self._git_tag(repo_directory, tag)

            git_push(
                '--tag',
                _out=sys.stdout,
                _cwd=repo_directory
            )

        except sh.ErrorReturnCode as error:  # pylint: disable=undefined-variable
            raise RuntimeError('Error invoking git push and argocd sync') from error

    @staticmethod
    def _git_tag(repo_directory, git_tag_value):  # pragma: no cover
        try:
            # NOTE:
            # this force is only needed locally in case of a re-reun of the same pipeline
            # without a fresh check out. You will notice there is no force on the push
            # making this an acceptable work around to the issue since on the off chance
            # actually overwriting a tag with a different comment, the push will fail
            # because the tag will be attached to a different git hash.
            sh.git.tag(  # pylint: disable=no-member
                git_tag_value,
                '-f',
                _out=sys.stdout,
                _err=sys.stderr,
                _cwd=repo_directory
            )
        except sh.ErrorReturnCode as error:  # pylint: disable=undefined-variable
            raise RuntimeError('Error invoking git tag ' + git_tag_value) from error

    def _get_app_name(self):
        repo_branch = self._get_repo_branch()
        organization = self.get_value('organization')
        application = self.get_value('application-name')
        service = self.get_value('service-name')
        app_name = f"{organization}-{application}-{service}-{repo_branch}"

        if self.environment:
            app_name = app_name + '-' + self.environment

        # repalce dangerous characters in app name
        app_name = app_name.lower()
        app_name = re.sub(
            KUBE_LABEL_NOT_SAFE_CHARS_REGEX,
            KUBE_LABEL_REPLACEMENT_CHAR,
            app_name
        )

        # max length for a kube label / resource name is 63
        if len(app_name) > KUBE_LABEL_MAX_LENGTH:
            app_name = app_name[len(app_name) - KUBE_LABEL_MAX_LENGTH:]

        # be sure app name doesn't start or end with not safe chars
        app_name = re.sub(
            KUBE_LABEL_NOT_SAFE_BEGINING_END_CHARS_REGEX,
            '',
            app_name
        )

        return app_name

    def _get_endpoint_url(self):
        argocd_app_name = self._get_app_name()
        endpoint_url = "{service}.{namespace}.{domain}".format(
            service=self.get_value('service-name'),
            namespace=argocd_app_name,
            domain=self.get_value('kube-app-domain')
        )
        return endpoint_url

    @staticmethod
    def _get_repo_branch():
        return sh.git('rev-parse', '--abbrev-ref', 'HEAD').rstrip()  # pylint: disable=too-many-function-args
