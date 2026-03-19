#!/usr/bin/env python3
"""
Console interface for Neo4j healthcare graph: queries, relationship creation, updates, deletes.
"""
from neo4j_connect import verify_connection
from neo4j_ops import (
    get_patients_with_diseases,
    get_doctors_and_specialties,
    get_doctors_treating_diseases,
    get_patient_appointments,
    get_hospitals_visited_by_patients,
    create_has_disease,
    create_treats,
    create_visits,
    create_at_hospital,
    update_patient_age,
    update_doctor_specialty,
    update_diagnosed_on,
    delete_patient,
    delete_patient_disease_relationship,
)


def print_records(records, empty_msg="No data found."):
    if not records:
        print(empty_msg)
        return
    for r in records:
        print("  ", r)


def menu():
    print("\n--- Neo4j Healthcare Graph ---")
    print("1 - Show all patients and their diseases")
    print("2 - Show doctors and specialties")
    print("3 - Show which doctors treat which diseases")
    print("4 - Show patient appointments")
    print("5 - Add relationship (Patient → Disease)")
    print("6 - Update patient age")
    print("7 - Delete patient")
    print("8 - Exit")
    return input("Choice [1-8]: ").strip()


def run():
    try:
        verify_connection()
    except Exception as e:
        print(f"Cannot connect to Neo4j: {e}")
        return

    while True:
        choice = menu()
        if not choice:
            continue

        try:
            if choice == "1":
                records = get_patients_with_diseases()
                print("\nPatients and their diseases:")
                print_records(records, "No patients or disease links found.")

            elif choice == "2":
                records = get_doctors_and_specialties()
                print("\nDoctors and specialties:")
                print_records(records, "No doctors found.")

            elif choice == "3":
                records = get_doctors_treating_diseases()
                print("\nDoctors treating diseases:")
                print_records(records, "No doctor–disease TREATS relationships found.")

            elif choice == "4":
                patient_id = input("Patient id: ").strip()
                if not patient_id:
                    print("Patient id required.")
                    continue
                records = get_patient_appointments(patient_id)
                print(f"\nAppointments for patient {patient_id}:")
                print_records(records, "No appointments found for this patient.")

            elif choice == "5":
                patient_id = input("Patient id: ").strip()
                disease_id = input("Disease id: ").strip()
                diagnosed_on = input("Diagnosed on (date, optional): ").strip() or None
                if not patient_id or not disease_id:
                    print("Patient id and Disease id required.")
                    continue
                result = create_has_disease(patient_id, disease_id, diagnosed_on)
                if result:
                    print("HAS_DISEASE relationship created/updated.")
                else:
                    print("Create failed (check that Patient and Disease nodes exist).")

            elif choice == "6":
                patient_id = input("Patient id: ").strip()
                age_str = input("New age: ").strip()
                if not patient_id or not age_str:
                    print("Patient id and age required.")
                    continue
                try:
                    age = int(age_str)
                except ValueError:
                    print("Age must be a number.")
                    continue
                result = update_patient_age(patient_id, age)
                if result:
                    print("Patient age updated.")
                else:
                    print("Update failed (patient may not exist).")

            elif choice == "7":
                patient_id = input("Patient id to delete: ").strip()
                if not patient_id:
                    print("Patient id required.")
                    continue
                confirm = input(f"Delete patient {patient_id} and all relationships? [y/N]: ").strip().lower()
                if confirm != "y":
                    print("Cancelled.")
                    continue
                delete_patient(patient_id)
                print("Patient deleted.")

            elif choice == "8":
                print("Bye.")
                break

            else:
                print("Invalid choice. Enter 1–8.")
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    run()
