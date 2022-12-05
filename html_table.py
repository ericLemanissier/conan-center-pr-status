import textwrap
from io import TextIOWrapper
from typing import List, Generator
from contextlib import contextmanager


@contextmanager
def html_table(file_name: str, thead: List[str]) -> Generator[TextIOWrapper, None, None]:
    with open(file_name, "wt", encoding="latin_1") as file_obj:
        file_obj.write(textwrap.dedent("""\
            <!DOCTYPE html>
            <html lang="en">

            <head>
                <title>ConanCenter - summary</title>
                <link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/1.12.1/css/jquery.dataTables.min.css"/>
                <style>
                    tr td {
                        white-space: nowrap;
                    }
                </style>
            </head>

            <body>
                <script src="https://code.jquery.com/jquery-3.6.0.slim.min.js"
                        crossorigin="anonymous"></script>
                <script type="text/javascript" src="https://cdn.datatables.net/1.12.1/js/jquery.dataTables.min.js"></script>
                <script>
                    $(document).ready( function () {

                        // Setup - add a text input to each footer cell
                        $('#summary tfoot th').each(function () {
                            var title = $(this).text();
                            $(this).html('<input type="text" placeholder="Filter ' + title + '" style="width:100%"/>');
                        });

                        $('#summary').DataTable({
                            scrollX: true,
                            scrollY: '80vh',
                            scrollCollapse: true,
                            paging: false,
                            order: [[8, 'desc']],
                            initComplete: function () {
                                // Apply the search
                                this.api()
                                    .columns()
                                    .every(function () {
                                        var that = this;

                                        $('input', this.footer()).on('keyup change clear', function () {
                                            if (that.search() !== this.value) {
                                                that.search(this.value).draw();
                                            }
                                        });
                                    });
                            },
                        });
                    } );
                </script>
                <table id="summary" class="stripe hover order-column row-border compact nowrap" style="width:100%">
                """))
        file_obj.write("<thead><tr>")
        for cell in thead:
            file_obj.write(f"<th>{cell}</th>" if cell else "<th/>")
        file_obj.write("</tr></thead>")
        file_obj.write("<tbody>")
        try:
            yield file_obj
        finally:
            file_obj.write("</tbody>")
            file_obj.write("<tfoot><tr>")
            for cell in thead:
                file_obj.write(f"<th>{cell}</th>" if cell else "<th/>")
            file_obj.write("</tr></tfoot>")
            file_obj.write("</table></body></html>")
