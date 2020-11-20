"""
Abstract class and helper constants for WorkflowResult
"""
import pickle
import os
import json
import yaml

from tssc.step_result import StepResult
from tssc.exceptions import TSSCException


class WorkflowResult:
    """
    Class to manage a list of StepResults.
    The WorkflowResult represents ALL previous results.
    """

    def __init__(self):
        self.__workflow_list = []

    @property
    def workflow_list(self):
        """
        Return workflow_list
        """
        return self.__workflow_list

    def get_artifact_value(self, artifact, step_name=None, sub_step_name=None):
        """
        Search for an artifact.
        1.  if step_name is provided, look for the artifact in the step
        2.  elif step_name and sub_step_name is provided, look for the artifact in the step/sub_step
        3.  else, search ALL steps for the FIRST match of the artifact.

        Parameters
        ----------
        artifact: str
           The artifact name to search for
        step_name: str optional
           Optionally search only in one step
        sub_step_name: str optional
            Optionally search only in one step

        Returns
        -------
        Str
           'v1.0.2'
        """
        value = None
        if step_name is not None:
            #  Look for step and sub_step
            if sub_step_name is not None:
                for step_result in self.workflow_list:
                    if step_result.step_name == step_name:
                        if step_result.sub_step_name == sub_step_name:
                            value = step_result.get_artifact_value(name=artifact)
                            break
            #  Look for step
            else:
                for step_result in self.workflow_list:
                    if step_result.step_name == step_name:
                        value = step_result.get_artifact_value(name=artifact)
                        if value is not None:
                            break
        #  Look for first occurrence
        else:
            for step_result in self.workflow_list:
                value = step_result.get_artifact_value(name=artifact)
                if value is not None:
                    break
        return value

    def get_step_result(self, step_name):
        """
        Search for a step by name step_result.

        Parameters
        ----------
        step_name : str
             Name of step to search

        Returns
        -------
        dict
             StepResult dictionary, eg:
            'tssc-results': {
                'step-name': {
                    'sub-step-name': {
                        'step-implementer-name': 'one',
                        'sub-step-name': '',
                        'success': True,
                        'message': '',
                        'artifacts': {
                            'a': {
                                'description': 'aA',
                                'type': 'str',
                                'value': 'A'
                            },
                            'b': {
                                'description': 'bB',
                                'type': 'file',
                                'value': 'B'
                            }
                        }
                    }
                }
            }
        """
        result = {}
        for step_result in self.workflow_list:
            if step_result.step_name == step_name:
                result[step_result.sub_step_name] = step_result.get_sub_step_result()

        return {'tssc-results': {step_name: result}}

    def add_step_result(self, step_result):
        """
        Add a single step_result to the workflow list.
        If the new result step is not already in the list
           - simply append, done
        Else
           - find the old step result
           - merge the old artifacts into the new artifacts
           - delete the old step result
           - append the new step result
           - note: the delete/append is needed because it is a list

        Parameters
        ----------
        step_result : StepResult
           An StepResult object to add to the list

        Raises
        ------
        Raises a TSSCException if an instance other than
        StepResult is passed as a parameter
        """

        if isinstance(step_result, StepResult):

            step_name = step_result.step_name
            sub_step_name = step_result.sub_step_name

            step_old = self.get_step_result_by_step_name(
                step_name=step_name,
                sub_step_name=sub_step_name)
            if step_old:
                merged = WorkflowResult.merge_results(step_result.artifacts, step_old.artifacts)
                step_result.artifacts.update(merged)
                self.delete_step_result_by_name(step_name=step_name)

            self.workflow_list.append(step_result)

        else:
            raise TSSCException('expect StepResult instance type')

    # ARTIFACT helpers:
    def write_results_to_yml_file(self, yml_filename):
        """
        Write the workflow list in a yaml format to file

        Parameters
        ----------
        yml_filename : str
             Name of file to write (eg: tssc-results/tssc-results.yml)

        Raises
        ------
        Raises a RuntimeError if the file cannot be dumped
        """
        try:
            WorkflowResult.folder_create(yml_filename)
            with open(yml_filename, 'w') as file:
                results = self.get_all_step_results()
                yaml.dump(results, file, indent=4)
        except Exception as error:
            raise RuntimeError(f'error dumping {yml_filename}: {error}') from error

    def write_results_to_json_file(self, json_filename):
        """
        Write the workflow list in a json format to file

        Parameters
        ----------
        json_filename : str
             Name of file to write (eg: tssc-results.json)

        Raises
        ------
        Raises a RuntimeError if the file cannot be dumped
        """
        try:
            WorkflowResult.folder_create(json_filename)
            with open(json_filename, 'w') as file:
                results = self.get_all_step_results()
                json.dump(results, file, indent=4)
        except Exception as error:
            raise RuntimeError(f'error dumping {json_filename}: {error}') from error

    # File handlers

    @staticmethod
    def load_from_pickle_file(pickle_filename):
        """
        Return the contents of a pickled file
        The file is expected to contain WorkflowResult instances

        Parameters
        ----------
        pickle_filename: str
           Name of the file to load

        Raises
        ------
        Raises a TSSCException if the file cannot be loaded
        Raises a TSSCException if the file contains non WorkflowResult instances
        """
        try:
            WorkflowResult.folder_create(filename=pickle_filename)

            # if the file does not exist return empty object
            if not os.path.isfile(pickle_filename):
                return WorkflowResult()

            # if the file is empty return empty object
            if os.path.getsize(pickle_filename) == 0:
                return WorkflowResult()

            # check that the file has Workflow object
            with open(pickle_filename, 'rb') as file:
                workflow_result = pickle.load(file)
                if not isinstance(workflow_result, WorkflowResult):
                    raise TSSCException(f'error {pickle_filename} has invalid data')
                return workflow_result

        except Exception as error:
            raise TSSCException(f'error loading {pickle_filename}: {error}') from error

    def write_to_pickle_file(self, pickle_filename):
        """
        Write the workflow list in a pickle format to file

        Parameters
        ----------
        pickle_filename : str
             Name of file to write (eg: tssc-results.pkl)

        Raises
        ------
        Raises a RuntimeError if the file cannot be dumped
        """
        try:
            WorkflowResult.folder_create(pickle_filename)
            with open(pickle_filename, 'wb') as file:
                pickle.dump(self, file)
        except Exception as error:
            raise RuntimeError(f'error dumping {pickle_filename}: {error}') from error

    # PRIVATE Helpers helpers

    def get_all_step_results(self):
        """
        Return a dictionary named tssc-results of all the step results in memory
        Specifically using 'tssc-results'.

        Returns
        -------
        results: dict
            results of all steps from list
        """
        all_results = {}
        for i in self.workflow_list:
            all_results = WorkflowResult.merge_results(all_results, i.get_step_result())
        tssc_results = {
            'tssc-results': all_results
        }
        return tssc_results

    def get_step_result_by_step_name(self, step_name, sub_step_name):
        """
        Helper method to return a step result by step_name and sub_step_name

        Parameters
        ----------
        step_name: str
            Name of step to search for
        sub_step_name: str
            Name of sub step to search for

        Returns
        -------
        StepResult
        """
        for current in self.workflow_list:
            if current.step_name == step_name:
                if current.sub_step_name == sub_step_name:
                    return current
        return None

    def delete_step_result_by_name(self, step_name):
        """
        Helper method to delete a step from the workflow list.

        Parameters
        ----------
        step_name: str
            Name of the step to remove from the workflow list
        """
        for i, current in enumerate(self.workflow_list):
            if current.step_name == step_name:
                del self.workflow_list[i]

    @staticmethod
    def folder_create(filename):
        """
        Helper method to create folder if it does not exist.

        Parameters
        ----------
        filename: str
            Absolute name of a file.ext
        """
        path = os.path.dirname(filename)
        if path and not os.path.exists(path):
            os.makedirs(os.path.dirname(filename))

    @staticmethod
    def merge_results(dict1, dict2):
        """
        Merge dictionary of dictionaries.
        Example:
            dict1 = {
              'A': {'value': 'A'},
              'C': {'value': 'overwriteme'}
            }
            dict2 = {
              'B': {'value': 'B'},
              'C': {'value': 'C'}
            }
            expected_answer = {
              'A': {'value': 'A'},
              'C': {'value': 'C'},
              'B': {'value': 'B'}
            }
        Parameters
        ----------
        dict1: dict
           original dictionary
        dict2: dict
           new dictionary to update and merge into original dictionary
        Returns
        -------
        dict
          returns merged dictionary
        """
        output = {}
        intersection = {**dict2, **dict1}

        for k_intersect, v_intersect in intersection.items():
            if k_intersect not in dict1:
                v_dict2 = dict2[k_intersect]
                output[k_intersect] = v_dict2

            elif k_intersect not in dict2:
                output[k_intersect] = v_intersect

            elif isinstance(v_intersect, dict):
                v_dict2 = dict2[k_intersect]
                output[k_intersect] = WorkflowResult.merge_results(v_intersect, v_dict2)

            else:
                output[k_intersect] = v_intersect

        return output