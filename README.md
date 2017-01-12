# DRF Deep

_DRF Deep_ makes using embedded resources in DRF as easy as pie.

## What Deep does that DRF doesn't do

With Django REST Framework alone, you can only read embedded resources from the
API, like this order:

```
{
  "id": 2,
  "customer": "Tom Christie",
  "items": [
    {"id": 1, "name": "Pizza Salami", "quantity": 3},
    {"id": 2, "name": "Pizza Funghi", "quantity": 1},
  ]
}
```

However, as soon as you plan to _write back_ such data, DRF tells you that it
can't do it. The reason given by the author, Tom Christie, is that there are
multiple strategies on how to interpret such requests.

While he's right, there's one strategy that works just great. See the following
PUT example requests that demonstrate the behavior.

**Scenario 1: Create new embedded resources**

```
{
  "items": [
    {"name": "Pizza Salami", "quantity": 3},
    {"name": "Pizza Funghi", "quantity": 1},
  ]
}
```

Because the embedded item resources do not contain an `id` field, DRF Deep
assumes that the caller wants to create new resources.

**Scenario 2: Update embedded resources**

```
{
  "items": [
    {"id": 1, "name": "Pizza Salami", "quantity": 3},
    {"id": 2, "name": "Pizza Funghi", "quantity": 1},
  ]
}
```

Now the embedded items both contain `id` fields, meaning DRF Deep will fetch
the existing items from the database and update them accordingly.

**Scenario 3: Deleting embedded resources**

```
{
  "items": [
    {"id": 2, "name": "Pizza Funghi", "quantity": 1},
  ]
}
```

The item with the ID of _2_ will be updated, like explained before. However the
pizza _#1_ is missing from the request, so DRF Deep will also drop it from the
database.

**Scenario 4: Not touching embedded resources**

```
{
  "customer": "Stefan Schindler"
}
```

As no `items` field is present, DRF Deep will do nothing with the items of this
order. Instead only the `customer` data is updated.

## Quickstart

```
pip install djangorestframework-deep
```

In your _serializers.py_:

```
from rest_framework.serializers import ModelSerializer
from rest_framework_deep.mixins import EmbeddedMixin

class ItemSerializer(ModelSerializer):
  class Meta:
    model = Item
    fields = ("id", "name", "quantity")

class OrderSerializer(EmbeddedMixin, ModelSerializer):
  class Meta:
    model = Order
    fields = ("id", "customer", "items")
    embedded_fields = {"items": ItemSerializer}
```

That's it!

## Contact

  * [GitHub](https://github.com/stschindler/djangorestframework-deep)
  * [@stschindler](https://twitter.com/stschindler) (Twitter)
  * [drfdeep@stschindler.io](mailto:drfdeep@stschindler.io)
