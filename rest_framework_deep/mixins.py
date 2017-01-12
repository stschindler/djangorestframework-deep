from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.db.models import Manager, Q
from rest_framework.relations import ManyRelatedField
from rest_framework.serializers import BaseSerializer, ListSerializer
from rest_framework.utils import model_meta

from pprint import pprint

__all__ = ["EmbeddedMixin", "OptionalFieldsMixin"]

class EmbeddedMixin(object):
  """
  Enables a serializer to have embedded foreign resources. The embedded
  resources can both be queried and modified in one go.

  If an embedded resource contains an ID field, an existing resource will be
  fetched and updated. Otherwise a new resource is created. Existing resources
  are deleted if the ID is missing from the new data. If the embedded field is
  left out, nothing is changed at all.

  Creating and updating is isolated in a database transaction.

  Which fields are used as embedded resources is determined by the Meta
  variable `embedded_fields`. You can pass extra kwargs arguments to the
  embedded serializers by setting them in `embedded_fields_extra_kwargs`.

  Example:

  ```
  class OrderSerializer(EmbeddedMixin, ModelSerializer):
    class Meta:
      embedded_fields = {
        "items": OrderItemSerializer,
        "comments": OrderCommentSerializer,
      }
  ```
  """

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

    # DRF removes the PK field from embedded data because it's marked as
    # read-only. We need the ID for detecting if the user wants to update or
    # create an embedded object, though. Ergo we mark it as read+write, but not
    # required. As the ID will be popped by us from the data anyway, there are
    # no side effects.
    # Additionally, the context is being copied to the child serializers.

    model = self.Meta.model
    extra_kwargs = getattr(self.Meta, "extra_kwargs", {})
    extra_embedded_kwargs = \
      getattr(self.Meta, "embedded_fields_extra_kwargs", {})
    fields = self.fields

    for field_name, serializer_class in self.Meta.embedded_fields.items():
      field_extra_kwargs = {}
      field_extra_kwargs.update(extra_kwargs.get(field_name, {}))
      field_extra_kwargs.update(extra_embedded_kwargs.get(field_name, {}))

      # Instantiate the new embedded serializer and overwrite over existing
      # field entry.
      field = serializer_class(
        many=True, context=self.context, **field_extra_kwargs
      )
      fields[field_name] = field

      related_serializer = field.child
      related_model = related_serializer.Meta.model

      # Get related PK field and disable "read only" and set is as not
      # required.
      related_pk_name = related_model._meta.pk.name
      related_pk_field = related_serializer.fields[related_pk_name]
      related_pk_field.read_only = False
      related_pk_field.required = False

      # Drop the related field in the related model now, as it's redundant and
      # not required.
      #
      # For example, if `Order` contains `Item`, we don't want this:
      #   {"id": 2, "items": [{"id": 1, "order": 2}, ...]}
      #
      # Instead we want this:
      #   {"id": 2, "items": [{"id": 1}, ...]}

      model_field = getattr(model, field_name)
      related_field_name = model_field.field.name
      del related_serializer.fields[related_field_name]

      # The relation field itself is writable, but not required. If it's left
      # out, this mixin will simply ignore it.
      field.read_only = False
      field.required = False

  def _get_relational_fields(self):
    requested_fields = getattr(self.Meta, "embedded_fields", ())

    fields = dict([
      (name, field)
      for name, field in self.fields.items()
      if name in requested_fields
    ])

    return fields

  @transaction.atomic
  def create(self, data):
    fields = self._get_relational_fields()
    model = self.Meta.model
    object_data = {}

    # Iterate over all embedded fields and pop the data out, so it's not
    # processed by DRF's default serializing code.
    for field_name, field in fields.items():
      object_data[field_name] = data.pop(field_name, [])

    # Create the instance with all the "simple" data.
    instance = super().create(data)

    # Finally create all related objects.
    for field_name, field in fields.items():
      related_serializer = field.child
      model_field = getattr(model, field_name)
      related_field_name = model_field.field.name

      for related_data in object_data[field_name]:
        related_data[related_field_name] = instance
        related_serializer.create(related_data)

    return instance

  def is_deletable(self, instance, serializer):
    """
    Override this function in order to tell the mixin if a specific object is
    deletable or not. Undeletable objects will not be dropped from the
    database. This allows for some fine-grained security.

    `instance` is the instance to be deleted, `serializer` is the related
    serializer.

    Return False to prohibit deleting.
    """
    return True

  @transaction.atomic
  def update(self, instance, data):
    fields = self._get_relational_fields()
    model = self.Meta.model
    object_data = {}

    # Iterate over all embedded fields and pop the data out, so it's not
    # processed by DRF's default serializing code.
    for field_name, field in fields.items():
      field_object_data = data.pop(field_name, None)

      if field_object_data is not None:
        object_data[field_name] = field_object_data

    # Update the instance with all the "simple" data.
    super().update(instance, data)

    for field_name, related_data_list in object_data.items():
      creatable_data = []
      updatable_data = []

      field = fields[field_name]
      instance_field = getattr(instance, field_name)

      # Fetch all related instances and index by PK.
      related_instances = dict([
          (related_instance.pk, related_instance)
          for related_instance in instance_field.all()
      ])

      model_field = getattr(model, field_name)
      related_field_name = model_field.field.name

      for related_data in related_data_list:
        # Set the related field in the embedded resource to ourself, because we
        # are its "new" parent.
        related_data[related_field_name] = instance

        # Add data to be serialized into list to delay creation. New instances
        # with the same primary key might be created at this point, where old
        # ones have to be dropped at first.
        related_id = related_data.pop("id", None)

        if related_id is None:
          creatable_data.append(related_data)
        else:
          related_instance = related_instances.pop(related_id)
          updatable_data.append((related_instance, related_data))

      # Drop obsolete instances. Obsolete instances are all existing instances
      # that are still left in `related_instances`. All instances that got
      # updated were popped out from it already.
      related_serializer = field.child

      for obsolete_instance in related_instances.values():
        # Check if it's allowed to delete the instance.
        if self.is_deletable(obsolete_instance, related_serializer) is True:
          obsolete_instance.delete()

      # Finally create the embedded resources.
      for related_data in creatable_data:
        related_serializer.create(related_data)

      # Finally update the embedded resources.
      for related_instance, related_data in updatable_data:
        related_serializer.update(related_instance, related_data)

    return instance

class OptionalFieldsMixin(object):
  """
  ViewSet mixin that allows to declare fields as optional by overwriting
  `get_serializer()`, meaning they won't be emitted in the response by default.
  The caller has to explicitly request them.

  Fields can be declared as being optional by using the Meta variable
  `optional_fields`, which is a simple Python list.

  To include a field in the response, the caller has to include the field's
  name in the `include` GET parameter. Multiple fields can be delimited by
  commas. To include all fields, `*` can be used.

  Fields are only dropped when reading data, not when writing data. In the
  latter case, all fields are enabled.
  """

  def get_serializer(self, *args, **kwargs):
    serializer = super().get_serializer(*args, **kwargs)

    # If the user is writing, all fields are enabled.
    if self.request.method in ("PUT", "PATCH", "POST"):
      included_fields = ("*",)
    elif "include" in self.request.GET:
      included_fields = self.request.GET["include"].split(",")
    else:
      included_fields = ()

    if "*" not in included_fields:
      # Make sure to get the real serializer, not ListSerializer, which is the
      # case for `many=True` fields.
      if isinstance(serializer, ListSerializer) is True:
        field_serializer = serializer.child
      else:
        field_serializer = serializer

      serializer_fields = field_serializer.fields.keys()
      optional_fields = getattr(self, "optional_fields", [])

      remaining_fields = (
        serializer_fields &
        (set(optional_fields) - set(included_fields))
      )

      for field in remaining_fields:
        del field_serializer.fields[field]

    return serializer
